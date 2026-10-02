from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.core import AuditLog, DataSource, EvidenceItem, SourceDocument
from app.modules.sources.embedding import (
    EMBEDDING_MODEL_ID,
    EmbeddingProvider,
    get_embedding_provider,
)
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
    SourceIndexRequest,
    SourceIndexResponse,
    SourceUploadRequest,
    SourceUploadResponse,
)
from app.modules.sources.storage import SourceContentStore

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
    embedding_provider: EmbeddingProvider | None = None,
    content_store: SourceContentStore | None = None,
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
            _validate_existing_source(existing.source, request)
            existing_period = existing.document.document_metadata.get("reporting_period_id")
            requested_period = str(request.reporting_period_id) if request.reporting_period_id else None
            if existing_period is not None and existing_period != requested_period:
                raise SourceConflictError(
                    "This source content is already bound to a different reporting period.",
                    code="source_identity_conflict",
                )
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
        provider = embedding_provider or get_embedding_provider()
        vectors = await provider.embed([chunk.text for chunk in chunks])
        storage_uri = await (content_store or SourceContentStore()).put(
            request.company_id, checksum, content_bytes
        )

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
                    "embedding_model_id": provider.model_id,
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
            "embedding_model_id": provider.model_id,
            "source_checksum": checksum,
            "trust_status": "synthetic" if request.is_synthetic else "accepted",
            "source_version": version,
        }
        document = SourceDocument(
            company_id=request.company_id,
            data_source_id=source.id,
            filename=request.filename,
            content_type=request.content_type,
            checksum=checksum,
            version=version,
            size_bytes=len(content_bytes),
            storage_uri=storage_uri,
            document_metadata=document_metadata,
        )
        repo.add(document)
        await repo.flush()

        embedded_at = datetime.now(UTC)
        evidence_items: list[EvidenceItem] = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk_bytes = chunk.text.encode("utf-8")
            evidence_metadata = {
                **context_metadata,
                "source_document_id": str(document.id),
                "ingestion_method_id": INGESTION_METHOD_ID,
                "extraction_method_id": extracted.extraction_method_id,
                "chunking_method_id": CHUNKING_METHOD_ID,
                "embedding_model_id": provider.model_id,
                "trust_status": "synthetic" if request.is_synthetic else "accepted",
                "source_version": version,
                "source_checksum": checksum,
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
                embedding=vector,
                embedding_model=provider.model_id,
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
                        "embedding_model_id": provider.model_id,
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
    except BaseException:
        await repo.rollback()
        raise


async def read_source_content(
    session: AsyncSession,
    *,
    company_id: UUID,
    document_id: UUID,
    content_store: SourceContentStore | None = None,
) -> tuple[SourceDocument, bytes]:
    stored = await SourceRepository(session).get_document(
        company_id=company_id, document_id=document_id
    )
    if stored is None:
        raise SourceContextNotFoundError("The source document was not found.")
    document = stored.document
    if document.storage_uri != f"content://{company_id}/{document.checksum}":
        raise SourceContextNotFoundError(
            "Original bytes were not retained for this source document.",
            code="source_content_not_found",
        )
    content = await (content_store or SourceContentStore()).read(
        company_id, document.checksum, int(document.size_bytes or 0)
    )
    return document, content


async def index_source_evidence(
    session: AsyncSession,
    *,
    document_id: UUID,
    request: SourceIndexRequest,
    trace_id: str | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> SourceIndexResponse:
    """Explicitly reindex original evidence; existing facts retain their old hashes.

    Changing a model or vector changes Assurance source hashes, so existing drafts
    and previews become stale instead of silently reinterpreting their evidence.
    """
    repo = SourceRepository(session)
    try:
        if await repo.get_actor(company_id=request.company_id, actor_id=request.actor_id) is None:
            raise SourceContextNotFoundError("The source actor was not found.")
        stored = await repo.get_document(
            company_id=request.company_id, document_id=document_id, for_update=True
        )
        if stored is None:
            raise SourceContextNotFoundError("The source document was not found.")
        if stored.source.status != "ready":
            raise SourceConflictError("Only ready source evidence can be indexed.")
        provider = embedding_provider or get_embedding_provider()
        pending = [
            item
            for item in stored.evidence
            if item.embedding_model != provider.model_id
            or item.embedding is None
            or item.embedded_at is None
        ]
        if len(pending) > 128:
            raise SourceUploadError(
                "The document exceeds the bounded indexing limit.", status_code=422
            )
        for item in pending:
            if _sha256(item.content_text.encode("utf-8")) != item.checksum:
                raise SourceChecksumMismatchError("Evidence content integrity validation failed.")
        vectors = await provider.embed([item.content_text for item in pending])
        now = datetime.now(UTC)
        for item, vector in zip(pending, vectors, strict=True):
            item.embedding, item.embedding_model, item.embedded_at = vector, provider.model_id, now
            metadata = dict(item.evidence_metadata)
            metadata.update(
                embedding_model_id=provider.model_id,
                embedding_status="indexed",
                source_version=stored.document.version,
                source_checksum=stored.document.checksum,
            )
            metadata.setdefault(
                "trust_status", "synthetic" if stored.source.is_synthetic else "accepted"
            )
            item.evidence_metadata = metadata
        if pending:
            document_metadata = dict(stored.document.document_metadata)
            document_metadata["embedding_model_id"] = provider.model_id
            stored.document.document_metadata = document_metadata
            repo.add(
                AuditLog(
                    company_id=request.company_id,
                    actor_id=request.actor_id,
                    agent_run_id=None,
                    action="source.evidence_indexed",
                    entity_type="source_document",
                    entity_id=document_id,
                    trace_id=trace_id,
                    details={
                        "embedding_model_id": provider.model_id,
                        "indexed_count": len(pending),
                    },
                )
            )
        await repo.commit()
        return SourceIndexResponse(
            document_id=document_id,
            embedding_model_id=provider.model_id,
            indexed_count=len(pending),
            replayed=not pending,
        )
    except BaseException:
        await repo.rollback()
        raise
