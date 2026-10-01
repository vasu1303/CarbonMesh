from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.core import AuditLog, DataSource, EvidenceItem, SourceDocument
from app.modules.sources.embedding import EMBEDDING_MODEL_ID, hash_embedding
from app.modules.sources.errors import (
    SourceChecksumMismatchError,
    SourceConflictError,
    SourceContextNotFoundError,
    SourceStorageUnavailableError,
    SourceUploadError,
)
from app.modules.sources.extraction import (
    CHUNKING_METHOD_ID,
    chunk_text,
    decode_content,
    extract_content_bytes,
    extraction_method_id,
)
from app.modules.sources.repository import (
    SourceRepository,
    SourceRepositoryProtocol,
    StoredSourceUpload,
)
from app.modules.sources.schemas import (
    DataSourceMetadata,
    EvidenceMetadata,
    SourceDocumentMetadata,
    SourceUploadRequest,
    SourceUploadResponse,
)

INGESTION_METHOD_ID = "carbonmesh-source-json-v1"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _context_metadata(request: SourceUploadRequest) -> dict[str, Any]:
    metadata = dict(request.metadata)
    metadata["company_id"] = str(request.company_id)
    if request.site_id is not None:
        metadata["site_id"] = str(request.site_id)
    if request.reporting_period_id is not None:
        metadata["reporting_period_id"] = str(request.reporting_period_id)
    if request.actor_id is not None:
        metadata["actor_id"] = str(request.actor_id)
    metadata["synthetic"] = request.is_synthetic
    return metadata


async def _validate_context(
    repository: SourceRepositoryProtocol,
    request: SourceUploadRequest,
) -> None:
    if await repository.get_company(company_id=request.company_id) is None:
        raise SourceContextNotFoundError(
            "The source company was not found or is inactive.",
            field_details={"company_id": "not_found"},
        )
    if (
        request.site_id is not None
        and await repository.get_site(
            company_id=request.company_id,
            site_id=request.site_id,
        )
        is None
    ):
        raise SourceContextNotFoundError(
            "The source site was not found in the requested company.",
            field_details={"site_id": "not_found"},
        )
    if (
        request.reporting_period_id is not None
        and await repository.get_reporting_period(
            company_id=request.company_id,
            reporting_period_id=request.reporting_period_id,
        )
        is None
    ):
        raise SourceContextNotFoundError(
            "The reporting period was not found in the requested company.",
            field_details={"reporting_period_id": "not_found"},
        )
    if (
        request.actor_id is not None
        and await repository.get_actor(
            company_id=request.company_id,
            actor_id=request.actor_id,
        )
        is None
    ):
        raise SourceContextNotFoundError(
            "The source actor was not found or is inactive in the requested company.",
            field_details={"actor_id": "not_found"},
        )


def _validate_existing_source(source: DataSource, request: SourceUploadRequest) -> None:
    mismatches: dict[str, str] = {}
    if source.site_id != request.site_id:
        mismatches["site_id"] = "does_not_match_existing_source"
    if source.source_type != request.source_type:
        mismatches["source_type"] = "does_not_match_existing_source"
    if source.is_synthetic != request.is_synthetic:
        mismatches["is_synthetic"] = "does_not_match_existing_source"
    if request.external_reference is not None and (
        source.external_reference != request.external_reference
    ):
        mismatches["external_reference"] = "does_not_match_existing_source"
    if source.status in {"failed", "archived"}:
        mismatches["source_name"] = f"existing_source_is_{source.status}"
    if mismatches:
        raise SourceConflictError(
            "The source name is already bound to incompatible source context.",
            code="source_identity_conflict",
            field_details=mismatches,
        )


def _response(
    stored: StoredSourceUpload,
    *,
    replayed: bool,
    decoded_size_bytes: int,
    default_extraction_method_id: str,
) -> SourceUploadResponse:
    source = stored.source
    document = stored.document
    document_metadata = dict(document.document_metadata)
    method_id = str(document_metadata.get("extraction_method_id", default_extraction_method_id))
    evidence = [
        EvidenceMetadata(
            id=item.id,
            source_document_id=item.source_document_id,
            evidence_type=item.evidence_type,
            locator=item.locator,
            checksum=item.checksum,
            metadata=dict(item.evidence_metadata),
            embedding_model=item.embedding_model,
            embedded_at=item.embedded_at,
        )
        for item in stored.evidence
    ]
    return SourceUploadResponse(
        replayed=replayed,
        ingestion_method_id=str(document_metadata.get("ingestion_method_id", INGESTION_METHOD_ID)),
        extraction_method_id=method_id,
        chunking_method_id=str(document_metadata.get("chunking_method_id", CHUNKING_METHOD_ID)),
        embedding_model_id=str(document_metadata.get("embedding_model_id", EMBEDDING_MODEL_ID)),
        decoded_size_bytes=decoded_size_bytes,
        evidence_count=len(evidence),
        source=DataSourceMetadata(
            id=source.id,
            company_id=source.company_id,
            site_id=source.site_id,
            name=source.name,
            source_type=source.source_type,
            status=source.status,
            external_reference=source.external_reference,
            is_synthetic=source.is_synthetic,
        ),
        document=SourceDocumentMetadata(
            id=document.id,
            data_source_id=document.data_source_id,
            filename=document.filename,
            content_type=document.content_type,
            checksum=document.checksum,
            version=document.version,
            size_bytes=int(document.size_bytes or 0),
            storage_uri=document.storage_uri,
            metadata=document_metadata,
        ),
        evidence=evidence,
    )


async def upload_source_document(
    session: AsyncSession,
    request: SourceUploadRequest,
    *,
    trace_id: str | None = None,
    repository: SourceRepositoryProtocol | None = None,
) -> SourceUploadResponse:
    """Validate, extract and atomically persist one evidence-backed source document."""

    repo = repository or SourceRepository(session)
    try:
        content_bytes = decode_content(content=request.content, encoding=request.encoding)
        checksum = _sha256(content_bytes)
        if request.checksum is not None and request.checksum != checksum:
            raise SourceChecksumMismatchError(
                "The supplied checksum does not match the decoded source content.",
                field_details={"checksum": "does_not_match_content"},
            )

        await _validate_context(repo, request)
        await repo.acquire_company_upload_lock(company_id=request.company_id)

        existing = await repo.get_upload_by_checksum(
            company_id=request.company_id,
            checksum=checksum,
        )
        if existing is not None:
            result = _response(
                existing,
                replayed=True,
                decoded_size_bytes=len(content_bytes),
                default_extraction_method_id=extraction_method_id(request.content_type),
            )
            await repo.commit()
            return result

        extracted = extract_content_bytes(
            content_bytes=content_bytes,
            content_type=request.content_type,
        )
        chunks = chunk_text(extracted.text)
        context_metadata = _context_metadata(request)

        source = await repo.get_source_by_name(
            company_id=request.company_id,
            name=request.source_name,
        )
        if source is None:
            source = DataSource(
                company_id=request.company_id,
                site_id=request.site_id,
                name=request.source_name,
                source_type=request.source_type,
                status="pending",
                external_reference=request.external_reference,
                configuration={
                    "ingestion_method_id": INGESTION_METHOD_ID,
                    "embedding_model_id": EMBEDDING_MODEL_ID,
                },
                is_synthetic=request.is_synthetic,
            )
            repo.add(source)
            await repo.flush()
        else:
            _validate_existing_source(source, request)

        version = await repo.next_document_version(data_source_id=cast(UUID, source.id))
        document_metadata = {
            **context_metadata,
            "ingestion_method_id": INGESTION_METHOD_ID,
            "extraction_method_id": extracted.extraction_method_id,
            "chunking_method_id": CHUNKING_METHOD_ID,
            "embedding_model_id": EMBEDDING_MODEL_ID,
            "source_checksum": checksum,
        }
        document = SourceDocument(
            company_id=request.company_id,
            data_source_id=source.id,
            filename=request.filename,
            content_type=request.content_type,
            checksum=checksum,
            version=version,
            size_bytes=len(content_bytes),
            storage_uri=f"sha256://{checksum}",
            document_metadata=document_metadata,
        )
        repo.add(document)
        await repo.flush()

        embedded_at = datetime.now(UTC)
        evidence_items: list[EvidenceItem] = []
        for chunk in chunks:
            chunk_bytes = chunk.text.encode("utf-8")
            evidence_metadata = {
                **context_metadata,
                "source_document_id": str(document.id),
                "ingestion_method_id": INGESTION_METHOD_ID,
                "extraction_method_id": extracted.extraction_method_id,
                "chunking_method_id": CHUNKING_METHOD_ID,
                "embedding_model_id": EMBEDDING_MODEL_ID,
                "chunk_index": chunk.index,
                "chunk_count": len(chunks),
                "character_start": chunk.start,
                "character_end": chunk.end,
            }
            item = EvidenceItem(
                company_id=request.company_id,
                source_document_id=document.id,
                evidence_type=request.evidence_type,
                locator=chunk.locator,
                content_text=chunk.text,
                checksum=_sha256(chunk_bytes),
                evidence_metadata=evidence_metadata,
                embedding=hash_embedding(chunk.text),
                embedding_model=EMBEDDING_MODEL_ID,
                embedded_at=embedded_at,
            )
            repo.add(item)
            evidence_items.append(item)

        if request.actor_id is not None:
            repo.add(
                AuditLog(
                    company_id=request.company_id,
                    actor_id=request.actor_id,
                    agent_run_id=None,
                    action="source.uploaded",
                    entity_type="source_document",
                    entity_id=document.id,
                    trace_id=trace_id,
                    details={
                        "source_checksum": checksum,
                        "decoded_size_bytes": len(content_bytes),
                        "evidence_count": len(evidence_items),
                        "content_type": request.content_type,
                        "source_type": request.source_type,
                        "ingestion_method_id": INGESTION_METHOD_ID,
                        "extraction_method_id": extracted.extraction_method_id,
                        "chunking_method_id": CHUNKING_METHOD_ID,
                        "embedding_model_id": EMBEDDING_MODEL_ID,
                    },
                )
            )

        await repo.flush()
        source.status = "ready"
        result = _response(
            StoredSourceUpload(source=source, document=document, evidence=evidence_items),
            replayed=False,
            decoded_size_bytes=len(content_bytes),
            default_extraction_method_id=extracted.extraction_method_id,
        )
        await repo.commit()
        return result
    except SourceUploadError:
        await repo.rollback()
        raise
    except SQLAlchemyError as error:
        await repo.rollback()
        raise SourceStorageUnavailableError("Source storage is temporarily unavailable.") from error
    except Exception:
        await repo.rollback()
        raise
