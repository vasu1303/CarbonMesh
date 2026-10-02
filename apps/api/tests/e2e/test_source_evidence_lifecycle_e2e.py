from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

import pytest
from pgvector.sqlalchemy import VECTOR
from sqlalchemy import select

from app.db.models.core import EvidenceItem, SourceDocument
from app.modules.assurance.repository import AssuranceRepository
from app.modules.assurance.retrieval import EvidenceRetrievalContext
from app.modules.sources.embedding import EMBEDDING_MODEL_ID, HashEmbeddingProvider, hash_embedding
from app.modules.sources.schemas import SourceIndexRequest
from app.modules.sources.service import index_source_evidence


@pytest.mark.asyncio
async def test_uploaded_original_download_is_exact_and_tenant_scoped(
    api_client, e2e_context
) -> None:
    ids = e2e_context.ids
    content = "Synthetic original disclosure evidence. Plant B boundary Q3 2026."
    response = await api_client.post(
        "/api/sources/upload",
        json={
            "company_id": str(ids.company_id),
            "site_id": str(ids.site_id),
            "reporting_period_id": str(ids.reporting_period_id),
            "actor_id": str(ids.analyst_id),
            "source_name": "Synthetic original evidence",
            "filename": "source.txt",
            "source_type": "synthetic",
            "content_type": "text/plain",
            "content": content,
            "evidence_type": "disclosure_support",
            "is_synthetic": True,
            "metadata": {"requirement_codes": ["S2-BOUNDARY"]},
        },
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["document"]["id"]
    downloaded = await api_client.get(
        f"/api/sources/{document_id}/content", params={"company_id": str(ids.company_id)}
    )
    assert downloaded.status_code == 200
    assert downloaded.content == content.encode()
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["cache-control"] == "private, no-store"
    wrong_tenant = await api_client.get(
        f"/api/sources/{document_id}/content", params={"company_id": str(uuid4())}
    )
    assert wrong_tenant.status_code == 404
    replay = await api_client.post(
        f"/api/sources/{document_id}/index",
        json={
            "company_id": str(ids.company_id),
            "actor_id": str(ids.analyst_id),
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["indexed_count"] == 0


@pytest.mark.asyncio
async def test_supplier_evidence_explicit_index_is_idempotent_and_preserves_content(
    api_client, e2e_context,
) -> None:
    ids = e2e_context.ids
    imported = await api_client.post("/api/imports/suppliers", json={
        "company_id": str(ids.company_id), "source_name": "Synthetic indexed supplier",
        "filename": "supplier.json", "content_type": "application/json", "is_synthetic": True,
        "content": {"products": [{
            "supplier_code": "INDEX-SUPPLIER", "supplier_name": "Synthetic supplier",
            "country_code": "IN", "product_code": "INDEX-PRODUCT", "name": "Synthetic product",
            "material_code": "RECYCLED-ALUMINIUM", "category": "metals",
            "pcf_kgco2e_per_unit": "2.0", "circularity_score": "80",
            "recycled_content_pct": "80", "recyclable_pct": "95",
            "evidence_quality_score": "90", "lead_time_days": 10, "unit_cost": "2.5",
            "currency": "USD", "effective_from": "2026-01-01",
            "evidence_text": "Synthetic supplier declaration for indexing.",
        }]},
    })
    assert imported.status_code == 201, imported.text
    assert imported.json()["accepted_count"] == 1
    document_id = UUID(imported.json()["source_document_id"])
    async with e2e_context.session_factory() as session:
        item = await session.scalar(select(EvidenceItem).where(EvidenceItem.source_document_id == document_id))
        assert item is not None and item.embedding is None
        original_text, original_checksum = item.content_text, item.checksum
        before_id = item.id
    request = SourceIndexRequest(company_id=ids.company_id, actor_id=ids.analyst_id)
    async with e2e_context.session_factory() as session:
        result = await index_source_evidence(session, document_id=document_id, request=request)
        assert result.indexed_count > 0
        assert result.embedding_model_id == EMBEDDING_MODEL_ID
    async with e2e_context.session_factory() as session:
        replay = await index_source_evidence(session, document_id=document_id, request=request)
        assert replay.replayed is True
        item = await session.get(EvidenceItem, before_id)
        assert item.content_text == original_text
        assert item.checksum == original_checksum
        assert item.embedding_model == EMBEDDING_MODEL_ID
        assert len(item.embedding) == 768


@pytest.mark.asyncio
async def test_context_and_trust_prefilter_prevents_topk_crowding_and_old_versions(
    e2e_context,
) -> None:
    ids = e2e_context.ids
    async with e2e_context.session_factory() as session:
        original = await session.get(EvidenceItem, ids.assurance_evidence_id)
        document = await session.get(SourceDocument, original.source_document_id)
        for index in range(105):
            metadata = dict(original.evidence_metadata)
            if index % 3 == 0:
                metadata["site_id"] = str(uuid4())
            elif index % 3 == 1:
                metadata["trust_status"] = "revoked"
            else:
                metadata["requirement_codes"] = ["OTHER"]
            session.add(
                EvidenceItem(
                    company_id=ids.company_id,
                    source_document_id=document.id,
                    evidence_type=original.evidence_type,
                    locator=f"aaa:{index:04d}",
                    content_text=original.content_text,
                    checksum=original.checksum,
                    evidence_metadata=metadata,
                    embedding=original.embedding,
                    embedding_model=original.embedding_model,
                    embedded_at=original.embedded_at,
                )
            )
        await session.commit()
        context = EvidenceRetrievalContext(
            company_id=ids.company_id,
            site_id=ids.site_id,
            reporting_period_id=ids.reporting_period_id,
            requirement_code="S2-BOUNDARY",
            allowed_evidence_types=("disclosure_support",),
            embedding_model=EMBEDDING_MODEL_ID,
            company_name="Maverick Manufacturing (synthetic)",
            site_name="Plant B",
            reporting_period_name="Q3 2026",
        )
        query = {
            "company_id": ids.company_id,
            "evidence_types": ("disclosure_support",),
            "embedding_model": EMBEDDING_MODEL_ID,
            "limit": 1,
            "query_embedding": hash_embedding(original.content_text),
            "context": context,
        }
        records = await AssuranceRepository(session).list_evidence_candidates(**query)
        assert [record.evidence.id for record in records] == [ids.assurance_evidence_id]
        if isinstance(EvidenceItem.__table__.c.embedding.type, VECTOR):
            assert records[0].similarity is not None
            assert records[0].similarity > 0.99
        session.add(
            SourceDocument(
                company_id=ids.company_id,
                data_source_id=document.data_source_id,
                filename="superseding.txt",
                content_type="text/plain",
                version=document.version + 1,
                checksum=hashlib.sha256(b"new evidence version").hexdigest(),
                size_bytes=20,
                document_metadata={"synthetic": True},
            )
        )
        await session.commit()
        assert await AssuranceRepository(session).list_evidence_candidates(**query) == []


@pytest.mark.asyncio
async def test_explicit_model_change_preserves_old_vector_until_success(e2e_context) -> None:
    from app.modules.sources.embedding import EmbeddingUnavailableError

    class FailingProvider:
        model_id = "new-test-model:768:v2"

        async def embed(self, texts):
            raise EmbeddingUnavailableError("Unavailable test provider")

    ids = e2e_context.ids
    async with e2e_context.session_factory() as session:
        original = await session.get(EvidenceItem, ids.assurance_evidence_id)
        original_document = await session.get(SourceDocument, original.source_document_id)
        document = SourceDocument(company_id=ids.company_id, data_source_id=original_document.data_source_id,
            filename="isolated-model-test.txt", content_type="text/plain", version=2,
            checksum=hashlib.sha256(b"isolated model test").hexdigest(), size_bytes=19,
            document_metadata={"synthetic": True})
        session.add(document)
        await session.flush()
        item = EvidenceItem(company_id=ids.company_id, source_document_id=document.id,
            evidence_type=original.evidence_type, locator="isolated:1", content_text=original.content_text,
            checksum=hashlib.sha256(original.content_text.encode("utf-8")).hexdigest(),
            evidence_metadata=dict(original.evidence_metadata),
            embedding=original.embedding, embedding_model=original.embedding_model,
            embedded_at=original.embedded_at)
        session.add(item)
        await session.commit()
        item_id, document_id, vector = item.id, document.id, list(item.embedding)
    async with e2e_context.session_factory() as session:
        with pytest.raises(EmbeddingUnavailableError):
            await index_source_evidence(
                session,
                document_id=document_id,
                request=SourceIndexRequest(company_id=ids.company_id, actor_id=ids.analyst_id),
                embedding_provider=FailingProvider(),
            )
    async with e2e_context.session_factory() as session:
        item = await session.get(EvidenceItem, item_id)
        assert item.embedding_model == EMBEDDING_MODEL_ID
        assert list(item.embedding) == vector
        provider = HashEmbeddingProvider()
        provider.model_id = "explicit-offline-v2"
        result = await index_source_evidence(
            session,
            document_id=document_id,
            request=SourceIndexRequest(company_id=ids.company_id, actor_id=ids.analyst_id),
            embedding_provider=provider,
        )
        assert result.embedding_model_id == "explicit-offline-v2"
