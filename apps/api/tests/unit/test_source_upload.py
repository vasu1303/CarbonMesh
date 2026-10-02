from __future__ import annotations

import asyncio
import base64
import hashlib
import math
from datetime import UTC, datetime
from io import BytesIO
from itertools import pairwise
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.routes.sources import router
from app.db.models.core import AuditLog, DataSource, EvidenceItem, SourceDocument
from app.dependencies.database import get_db_session
from app.modules.sources import extraction
from app.modules.sources.embedding import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_ID,
    hash_embedding,
)
from app.modules.sources.errors import (
    PdfProviderUnavailableError,
    SourceChecksumMismatchError,
    SourceConflictError,
    SourceContextNotFoundError,
    UnsupportedDocumentError,
)
from app.modules.sources.extraction import (
    CHUNKING_METHOD_ID,
    MAX_CHUNK_CHARACTERS,
    PDF_EXTRACTION_METHOD_ID,
    chunk_text,
    decode_content,
    extract_content_bytes,
)
from app.modules.sources.repository import StoredSourceUpload
from app.modules.sources.schemas import SourceUploadRequest, SourceUploadResponse
from app.modules.sources.service import INGESTION_METHOD_ID, upload_source_document

COMPANY_ID = UUID("00000000-0000-4000-8000-000000000001")
SITE_ID = UUID("00000000-0000-4000-8000-000000000002")
PERIOD_ID = UUID("00000000-0000-4000-8000-000000000003")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000004")


def _request(**updates: Any) -> SourceUploadRequest:
    values: dict[str, Any] = {
        "company_id": COMPANY_ID,
        "site_id": SITE_ID,
        "reporting_period_id": PERIOD_ID,
        "actor_id": ACTOR_ID,
        "source_name": "Assurance evidence",
        "filename": "evidence.md",
        "content_type": "text/markdown",
        "encoding": "utf-8",
        "content": "Plant B is the Q3 2026 reporting boundary.",
        "evidence_type": "disclosure_support",
        "metadata": {"requirement_codes": ["S2-BOUNDARY"], "company_id": "untrusted"},
        "external_reference": "client://assurance/evidence.md",
        "is_synthetic": True,
        "source_type": "synthetic",
    }
    values.update(updates)
    return SourceUploadRequest.model_validate(values)


class FakeRepository:
    def __init__(self) -> None:
        self.company_exists = True
        self.site_exists = True
        self.period_exists = True
        self.actor_exists = True
        self.existing: StoredSourceUpload | None = None
        self.source: DataSource | None = None
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.locks = 0

    async def get_company(self, *, company_id: UUID) -> object | None:
        assert company_id == COMPANY_ID
        return object() if self.company_exists else None

    async def get_site(self, *, company_id: UUID, site_id: UUID) -> object | None:
        assert company_id == COMPANY_ID
        assert site_id == SITE_ID
        return object() if self.site_exists else None

    async def get_reporting_period(
        self,
        *,
        company_id: UUID,
        reporting_period_id: UUID,
    ) -> object | None:
        assert company_id == COMPANY_ID
        assert reporting_period_id == PERIOD_ID
        return object() if self.period_exists else None

    async def get_actor(self, *, company_id: UUID, actor_id: UUID) -> object | None:
        assert company_id == COMPANY_ID
        assert actor_id == ACTOR_ID
        return object() if self.actor_exists else None

    async def acquire_company_upload_lock(self, *, company_id: UUID) -> None:
        assert company_id == COMPANY_ID
        self.locks += 1

    async def get_upload_by_checksum(
        self,
        *,
        company_id: UUID,
        checksum: str,
    ) -> StoredSourceUpload | None:
        assert company_id == COMPANY_ID
        assert len(checksum) == 64
        return self.existing

    async def get_source_by_name(self, *, company_id: UUID, name: str) -> DataSource | None:
        assert company_id == COMPANY_ID
        assert name == "Assurance evidence"
        return self.source

    async def next_document_version(self, *, data_source_id: UUID) -> int:
        assert isinstance(data_source_id, UUID)
        return 1

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        for instance in self.added:
            if (
                isinstance(instance, (DataSource, SourceDocument, EvidenceItem, AuditLog))
                and instance.id is None
            ):
                instance.id = uuid4()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def test_request_is_strict_and_pdf_requires_base64() -> None:
    assert _request().source_type == "synthetic"
    default_payload = _request().model_dump()
    default_payload.pop("source_type")
    default_payload.update(
        site_id=None,
        reporting_period_id=None,
        actor_id=None,
        is_synthetic=False,
    )
    assert SourceUploadRequest.model_validate(default_payload).source_type == "api"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        SourceUploadRequest.model_validate({**_request().model_dump(), "unexpected": True})
    with pytest.raises(ValidationError, match="base64"):
        SourceUploadRequest.model_validate(
            {
                **_request().model_dump(),
                "content_type": "application/pdf",
                "encoding": "utf-8",
            }
        )


def test_request_recursively_rejects_sensitive_metadata_keys() -> None:
    with pytest.raises(ValidationError, match="non-public field"):
        _request(metadata={"safe": [{"nested": {"Authorization": "do-not-store"}}]})


def test_paragraph_chunking_is_deterministic_bounded_and_overlapping() -> None:
    text = f"{'A' * 700}\n\n{'B' * 700}\n\n{'C' * 700}"

    first = chunk_text(text)
    second = chunk_text(text)

    assert first == second
    assert len(first) >= 3
    assert all(0 < len(chunk.text) <= MAX_CHUNK_CHARACTERS for chunk in first)
    assert [chunk.locator for chunk in first] == [
        f"chunk:{index:06d}" for index in range(1, len(first) + 1)
    ]
    for previous, current in pairwise(first):
        assert previous.text[-150:] == current.text[:150]
        assert current.start == previous.end - 150


def test_hash_embedding_is_stable_signed_and_l2_normalized() -> None:
    first = hash_embedding("Carbon mesh carbon operations")
    second = hash_embedding("Carbon mesh carbon operations")

    assert first == second
    assert len(first) == EMBEDDING_DIMENSIONS
    assert math.sqrt(sum(value * value for value in first)) == pytest.approx(1.0)
    assert any(value > 0 for value in first)
    assert any(value < 0 for value in first)
    assert hash_embedding("different evidence") != first


def test_pdf_bytes_use_optional_reader_without_utf8_decoding(monkeypatch) -> None:
    pdf_bytes = b"\xff\xfe%PDF synthetic bytes"

    class Page:
        def extract_text(self) -> str:
            return "Extracted PDF evidence"

    class Reader:
        def __init__(self, stream) -> None:
            assert stream.read() == pdf_bytes
            self.is_encrypted = False
            self.pages = [Page()]

    monkeypatch.setattr(extraction, "_load_pdf_reader", lambda: Reader)

    decoded = decode_content(
        content=base64.b64encode(pdf_bytes).decode("ascii"),
        encoding="base64",
    )
    result = extract_content_bytes(
        content_bytes=decoded,
        content_type="application/pdf",
    )

    assert decoded == pdf_bytes
    assert result.text == "Extracted PDF evidence"
    assert result.extraction_method_id == PDF_EXTRACTION_METHOD_ID


def test_real_pypdf_extracts_text() -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 200 Td (Real pypdf evidence) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)

    result = extract_content_bytes(
        content_bytes=output.getvalue(),
        content_type="application/pdf",
    )

    assert "Real pypdf evidence" in result.text
    assert result.extraction_method_id == PDF_EXTRACTION_METHOD_ID


def test_pdf_missing_extractor_is_typed_provider_unavailable(monkeypatch) -> None:
    def unavailable():
        raise PdfProviderUnavailableError("PDF extraction is unavailable.")

    monkeypatch.setattr(extraction, "_load_pdf_reader", unavailable)

    with pytest.raises(PdfProviderUnavailableError) as caught:
        extract_content_bytes(
            content_bytes=b"%PDF",
            content_type="application/pdf",
        )

    assert caught.value.code == "provider_unavailable"
    assert caught.value.status_code == 503


def test_extraction_rejects_nul_and_non_finite_json() -> None:
    with pytest.raises(UnsupportedDocumentError) as nul_error:
        extract_content_bytes(
            content_bytes=b"safe prefix\x00unsafe suffix",
            content_type="text/plain",
        )
    assert nul_error.value.field_details == {"content": "contains_nul"}

    for constant in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(UnsupportedDocumentError) as json_error:
            extract_content_bytes(
                content_bytes=f'{{"value":{constant}}}'.encode(),
                content_type="application/json",
            )
        assert json_error.value.field_details == {"content": "invalid_json"}


@pytest.mark.asyncio
async def test_service_persists_tenant_context_chunks_and_safe_response() -> None:
    repository = FakeRepository()
    request = _request()

    result = await upload_source_document(
        object(),  # type: ignore[arg-type]
        request,
        trace_id="source-upload-trace",
        repository=repository,  # type: ignore[arg-type]
    )

    assert repository.commits == 1
    assert repository.rollbacks == 0
    assert repository.locks == 1
    source = next(item for item in repository.added if isinstance(item, DataSource))
    document = next(item for item in repository.added if isinstance(item, SourceDocument))
    evidence = next(item for item in repository.added if isinstance(item, EvidenceItem))
    audit = next(item for item in repository.added if isinstance(item, AuditLog))
    assert source.status == "ready"
    assert document.checksum == hashlib.sha256(request.content.encode("utf-8")).hexdigest()
    assert document.storage_uri == f"content://{request.company_id}/{document.checksum}"
    assert evidence.embedding_model == EMBEDDING_MODEL_ID
    assert evidence.embedding is not None and len(evidence.embedding) == 768
    assert evidence.evidence_metadata == {
        "requirement_codes": ["S2-BOUNDARY"],
        "company_id": str(COMPANY_ID),
        "site_id": str(SITE_ID),
        "reporting_period_id": str(PERIOD_ID),
        "actor_id": str(ACTOR_ID),
        "synthetic": True,
        "source_document_id": str(document.id),
        "source_checksum": document.checksum,
        "source_version": 1,
        "trust_status": "synthetic",
        "ingestion_method_id": INGESTION_METHOD_ID,
        "extraction_method_id": "carbonmesh-text-utf8-v1",
        "chunking_method_id": CHUNKING_METHOD_ID,
        "embedding_model_id": EMBEDDING_MODEL_ID,
        "chunk_index": 1,
        "chunk_count": 1,
        "character_start": 0,
        "character_end": len(request.content),
    }
    assert audit.actor_id == ACTOR_ID
    assert audit.trace_id == "source-upload-trace"
    assert audit.action == "source.uploaded"
    assert audit.entity_type == "source_document"
    assert audit.entity_id == document.id
    assert audit.details == {
        "source_checksum": document.checksum,
        "decoded_size_bytes": len(request.content.encode()),
        "evidence_count": 1,
        "content_type": "text/markdown",
        "source_type": "synthetic",
        "ingestion_method_id": INGESTION_METHOD_ID,
        "extraction_method_id": "carbonmesh-text-utf8-v1",
        "chunking_method_id": CHUNKING_METHOD_ID,
        "embedding_model_id": EMBEDDING_MODEL_ID,
    }
    assert request.content not in str(audit.details)
    assert result.replayed is False
    assert result.evidence_count == 1
    serialized = result.model_dump_json()
    assert request.content not in serialized
    assert "embedding" not in result.evidence[0].model_dump()


@pytest.mark.asyncio
async def test_tenant_checksum_replay_returns_existing_without_reextracting_pdf(
    monkeypatch,
) -> None:
    repository = FakeRepository()
    pdf_bytes = b"%PDF existing"
    checksum = hashlib.sha256(pdf_bytes).hexdigest()
    source = DataSource(
        id=uuid4(),
        company_id=COMPANY_ID,
        site_id=SITE_ID,
        name="Existing source",
        source_type="api",
        status="ready",
        external_reference=None,
        configuration={},
        is_synthetic=False,
    )
    document = SourceDocument(
        id=uuid4(),
        company_id=COMPANY_ID,
        data_source_id=source.id,
        filename="existing.pdf",
        content_type="application/pdf",
        checksum=checksum,
        version=1,
        size_bytes=len(pdf_bytes),
        storage_uri=f"sha256://{checksum}",
        document_metadata={"extraction_method_id": PDF_EXTRACTION_METHOD_ID},
    )
    evidence = EvidenceItem(
        id=uuid4(),
        company_id=COMPANY_ID,
        source_document_id=document.id,
        evidence_type="disclosure_support",
        locator="chunk:000001",
        content_text="Existing extracted text",
        checksum=hashlib.sha256(b"Existing extracted text").hexdigest(),
        evidence_metadata={"company_id": str(COMPANY_ID)},
        embedding=[0.0] * 768,
        embedding_model=EMBEDDING_MODEL_ID,
        embedded_at=datetime.now(UTC),
    )
    repository.existing = StoredSourceUpload(source, document, [evidence])
    monkeypatch.setattr(
        extraction,
        "_load_pdf_reader",
        lambda: (_ for _ in ()).throw(AssertionError("PDF replay must not extract")),
    )

    result = await upload_source_document(
        object(),  # type: ignore[arg-type]
        _request(
            source_name="A different replay name",
            source_type="api",
            is_synthetic=False,
            filename="existing.pdf",
            content_type="application/pdf",
            encoding="base64",
            content=base64.b64encode(pdf_bytes).decode("ascii"),
            external_reference=None,
        ),
        repository=repository,  # type: ignore[arg-type]
    )

    assert result.replayed is True
    assert result.document.id == document.id
    assert repository.added == []
    assert repository.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [{"site_id": None}, {"reporting_period_id": None}, {"is_synthetic": False}])
async def test_checksum_replay_cannot_relabel_source_context(updates) -> None:
    repository = FakeRepository()
    await upload_source_document(object(), _request(), repository=repository)
    repository.existing = StoredSourceUpload(
        next(item for item in repository.added if isinstance(item, DataSource)),
        next(item for item in repository.added if isinstance(item, SourceDocument)),
        [item for item in repository.added if isinstance(item, EvidenceItem)],
    )
    with pytest.raises(SourceConflictError):
        await upload_source_document(object(), _request(**updates), repository=repository)
    assert repository.commits == 1
    assert repository.rollbacks == 1


@pytest.mark.asyncio
async def test_cancelled_embedding_rolls_back_source_upload_transaction() -> None:
    class CancelledProvider:
        model_id = "cancelled-test-provider"

        async def embed(self, texts):
            raise asyncio.CancelledError

    repository = FakeRepository()
    with pytest.raises(asyncio.CancelledError):
        await upload_source_document(object(), _request(), repository=repository,
                                     embedding_provider=CancelledProvider())
    assert repository.rollbacks == 1
    assert repository.commits == 0
    assert repository.added == []


@pytest.mark.asyncio
async def test_service_fails_closed_on_checksum_or_period_mismatch() -> None:
    checksum_repository = FakeRepository()
    with pytest.raises(SourceChecksumMismatchError):
        await upload_source_document(
            object(),  # type: ignore[arg-type]
            _request(checksum="0" * 64),
            repository=checksum_repository,  # type: ignore[arg-type]
        )
    assert checksum_repository.rollbacks == 1
    assert checksum_repository.added == []

    period_repository = FakeRepository()
    period_repository.period_exists = False
    with pytest.raises(SourceContextNotFoundError) as caught:
        await upload_source_document(
            object(),  # type: ignore[arg-type]
            _request(),
            repository=period_repository,  # type: ignore[arg-type]
        )
    assert caught.value.field_details == {"reporting_period_id": "not_found"}
    assert period_repository.rollbacks == 1
    assert period_repository.added == []


def _route_response() -> SourceUploadResponse:
    repository = FakeRepository()
    content = _request().content
    source_id = uuid4()
    document_id = uuid4()
    source = DataSource(
        id=source_id,
        company_id=COMPANY_ID,
        site_id=SITE_ID,
        name="Assurance evidence",
        source_type="synthetic",
        status="ready",
        external_reference=None,
        configuration={},
        is_synthetic=True,
    )
    document = SourceDocument(
        id=document_id,
        company_id=COMPANY_ID,
        data_source_id=source_id,
        filename="evidence.md",
        content_type="text/markdown",
        checksum=hashlib.sha256(content.encode()).hexdigest(),
        version=1,
        size_bytes=len(content.encode()),
        storage_uri="sha256://safe",
        document_metadata={},
    )
    evidence = EvidenceItem(
        id=uuid4(),
        company_id=COMPANY_ID,
        source_document_id=document_id,
        evidence_type="disclosure_support",
        locator="chunk:000001",
        content_text=content,
        checksum=hashlib.sha256(content.encode()).hexdigest(),
        evidence_metadata={},
        embedding=[0.0] * 768,
        embedding_model=EMBEDDING_MODEL_ID,
        embedded_at=datetime.now(UTC),
    )
    repository.existing = StoredSourceUpload(source, document, [evidence])
    from app.modules.sources.service import _response

    return _response(
        repository.existing,
        replayed=False,
        decoded_size_bytes=len(content.encode()),
        default_extraction_method_id="carbonmesh-text-utf8-v1",
    )


def _route_client() -> TestClient:
    application = FastAPI()
    application.include_router(router, prefix="/api")
    application.dependency_overrides[get_db_session] = lambda: object()
    return TestClient(application)


def test_source_upload_route_returns_metadata_only(monkeypatch) -> None:
    async def fake_upload(session, request, *, trace_id):
        assert session is not None
        assert request.reporting_period_id == PERIOD_ID
        assert trace_id == "route-source-trace"
        return _route_response()

    monkeypatch.setattr("app.api.routes.sources.upload_source_document", fake_upload)
    payload = _request().model_dump(mode="json")

    response = _route_client().post(
        "/api/sources/upload",
        json=payload,
        headers={"X-Trace-ID": "route-source-trace"},
    )

    assert response.status_code == 201
    assert response.json()["document"]["filename"] == "evidence.md"
    assert payload["content"] not in response.text


def test_source_upload_route_maps_typed_failures_to_safe_errors(monkeypatch) -> None:
    async def fake_upload(_session, _request, *, trace_id):
        assert trace_id == "source-test-trace"
        raise UnsupportedDocumentError(
            "The document cannot be extracted.",
            field_details={"content": "unsupported"},
        )

    monkeypatch.setattr("app.api.routes.sources.upload_source_document", fake_upload)

    response = _route_client().post(
        "/api/sources/upload",
        json=_request().model_dump(mode="json"),
        headers={"X-Trace-ID": "source-test-trace"},
    )

    assert response.status_code == 422
    assert response.headers["X-Trace-ID"] == "source-test-trace"
    assert response.json() == {
        "detail": {
            "code": "unsupported",
            "message": "The document cannot be extracted.",
            "trace_id": "source-test-trace",
            "retryable": False,
            "field_details": [{"field": "content", "detail": "unsupported"}],
        }
    }
