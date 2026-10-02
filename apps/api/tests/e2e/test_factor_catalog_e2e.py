from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models.carbon import EmissionFactor
from app.db.models.core import AuditLog, Company, DataSource, EvidenceItem, SourceDocument
from app.db.models.ledger import LedgerEvent, LedgerEventEvidence
from app.modules.demo.fixtures import ACTIVITY_METRIC_ID, DEMO_ANALYST_ID, EMISSIONS_METRIC_ID
from app.modules.demo.service import reset_and_seed_demo
from app.modules.factors.repository import FactorRepository


async def _factor_request(client, context):
    ids = context.ids
    response = await client.post(
        "/api/sources/upload",
        json={
            "company_id": str(ids.company_id),
            "actor_id": str(ids.analyst_id),
            "source_name": "Synthetic material factor evidence",
            "filename": "factor.txt",
            "content_type": "text/plain",
            "source_type": "synthetic",
            "is_synthetic": True,
            "evidence_type": "emission_factor",
            "content": "Synthetic NEW-MATERIAL factor: 2.5 kgCO2e/kg.",
        },
    )
    assert response.status_code == 201, response.text
    return {
        "company_id": str(ids.company_id),
        "actor_id": str(ids.analyst_id),
        "metric_definition_id": "00000000-0000-4000-8000-000000000020",
        "evidence_item_id": response.json()["evidence"][0]["id"],
        "factor_code": "NEW-MATERIAL-PCF",
        "version": "1",
        "name": "Synthetic material PCF",
        "material_code": "NEW-MATERIAL",
        "product_code": "NEW-PRODUCT",
        "geography": "IN",
        "factor_value": "2.500000000000",
        "source_quality": "0.95",
        "factor_specificity": "1",
        "factor_recency": "1",
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
    }


@pytest.mark.asyncio
async def test_factor_registration_concurrency_exact_replay_and_immutable_versions(
    api_client, e2e_context
):
    payload = await _factor_request(api_client, e2e_context)
    first, second = await asyncio.gather(
        *[api_client.post("/api/emission-factors", json=payload) for _ in range(2)]
    )
    assert sorted([first.status_code, second.status_code]) == [200, 201], [first.text, second.text]
    assert first.json()["factor"]["id"] == second.json()["factor"]["id"]
    assert first.json()["ledger_event_id"] == second.json()["ledger_event_id"]
    equivalent = await api_client.post(
        "/api/emission-factors", json={**payload, "factor_value": "2.5"}
    )
    assert equivalent.status_code == 200, equivalent.text
    changed = await api_client.post(
        "/api/emission-factors", json={**payload, "factor_value": "2.6"}
    )
    assert changed.status_code == 409, changed.text
    version = await api_client.post(
        "/api/emission-factors", json={**payload, "version": "2", "factor_value": "2.6"}
    )
    assert version.status_code == 201, version.text
    listing = await api_client.get(
        "/api/emission-factors",
        params={
            "company_id": payload["company_id"],
            "material_code": "NEW-MATERIAL",
            "active": True,
            "limit": 1,
        },
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 2
    assert len(listing.json()["items"]) == 1
    assert Decimal(listing.json()["items"][0]["factor_value"]) == Decimal("2.5")
    async with e2e_context.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEvent)
                .where(LedgerEvent.event_type == "factor.registered")
            )
            == 2
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "factor.registered")
            )
            == 2
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEventEvidence)
                .where(LedgerEventEvidence.evidence_item_id == UUID(payload["evidence_item_id"]))
            )
            == 2
        )


@pytest.mark.asyncio
async def test_factor_foreign_evidence_invalid_metric_roles_and_precision_fail_closed(
    api_client, e2e_context
):
    payload = await _factor_request(api_client, e2e_context)
    async with e2e_context.session_factory() as session:
        evidence = await session.get(EvidenceItem, UUID(payload["evidence_item_id"]))
        document = await session.get(SourceDocument, evidence.source_document_id)
        foreign = Company(
            id=uuid4(), name="Synthetic other company", code="factor-other", is_synthetic=True
        )
        session.add(foreign)
        await session.flush()
        source = DataSource(
            company_id=foreign.id,
            name="Synthetic foreign factor",
            source_type="synthetic",
            status="ready",
            configuration={},
            is_synthetic=True,
        )
        session.add(source)
        await session.flush()
        foreign_document = SourceDocument(
            company_id=foreign.id,
            data_source_id=source.id,
            filename="foreign.txt",
            content_type="text/plain",
            checksum=document.checksum,
            version=1,
            document_metadata={"synthetic": True},
        )
        session.add(foreign_document)
        await session.flush()
        foreign_evidence = EvidenceItem(
            company_id=foreign.id,
            source_document_id=foreign_document.id,
            evidence_type="emission_factor",
            locator="chunk:1",
            content_text=evidence.content_text,
            checksum=evidence.checksum,
            evidence_metadata={"synthetic": True},
        )
        session.add(foreign_evidence)
        await session.commit()
        foreign_id = foreign_evidence.id
    for changes, expected in (
        ({"evidence_item_id": str(foreign_id)}, 404),
        ({"metric_definition_id": str(e2e_context.ids.assurance_metric_id)}, 422),
        ({"actor_id": str(e2e_context.ids.procurement_manager_id)}, 403),
        ({"factor_value": "NaN"}, 422),
        ({"factor_value": "0.0000000000001"}, 422),
        ({"factor_value": "1000000000000"}, 422),
        ({"denominator_unit": "kWh"}, 422),
        ({"source_quality": "1.00001"}, 422),
        ({"effective_to": "2025-01-01"}, 422),
    ):
        response = await api_client.post("/api/emission-factors", json={**payload, **changes})
        assert response.status_code == expected, response.text
    async with e2e_context.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(EmissionFactor)
                .where(EmissionFactor.factor_code == payload["factor_code"])
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEvent)
                .where(LedgerEvent.event_type == "factor.registered")
            )
            == 0
        )


@pytest.mark.asyncio
async def test_new_material_import_factor_registration_and_decimal_calculation(
    api_client, e2e_context
):
    # The disposable cluster receives foundation records only; all new facts use HTTP.
    async with e2e_context.session_factory() as session:
        seeded = await reset_and_seed_demo(session)
    context = {
        "company_id": str(seeded.company_id),
        "site_id": str(seeded.site_id),
        "reporting_period_id": str(seeded.reporting_period_id),
    }
    supplier = await api_client.post(
        "/api/imports/suppliers",
        json={
            "company_id": context["company_id"],
            "source_name": "Synthetic new material supplier",
            "filename": "new-supplier.json",
            "content_type": "application/json",
            "is_synthetic": True,
            "content": {
                "products": [
                    {
                        "supplier_code": "NEW-SUPPLIER",
                        "supplier_name": "Synthetic new supplier",
                        "country_code": "IN",
                        "product_code": "NEW-PRODUCT",
                        "name": "Synthetic new product",
                        "material_code": "NEW-MATERIAL",
                        "category": "metals",
                        "pcf_kgco2e_per_unit": "2.5",
                        "circularity_score": "80",
                        "recycled_content_pct": "80",
                        "recyclable_pct": "95",
                        "evidence_quality_score": "95",
                        "lead_time_days": 5,
                        "unit_cost": "3",
                        "currency": "USD",
                        "effective_from": "2026-01-01",
                        "evidence_text": "Synthetic NEW-MATERIAL factor: 2.5 kgCO2e/kg.",
                    }
                ]
            },
        },
    )
    assert supplier.status_code == 201, supplier.text
    assert supplier.json()["accepted_count"] == 1
    activity = await api_client.post(
        "/api/activities/import",
        json={
            **context,
            "metric_definition_id": str(ACTIVITY_METRIC_ID),
            "source_name": "Synthetic new material activity",
            "filename": "activity.json",
            "content_type": "application/json",
            "is_synthetic": True,
            "content": [
                {
                    "material_code": "NEW-MATERIAL",
                    "activity_date": "2026-08-15",
                    "quantity": "100",
                    "unit": "kg",
                    "supplier_code": "NEW-SUPPLIER",
                    "product_code": "NEW-PRODUCT",
                }
            ],
        },
    )
    assert activity.status_code == 201, activity.text
    assert activity.json()["accepted_count"] == 1
    calculation_request = {
        **context,
        "material_code": "NEW-MATERIAL",
        "actor_id": str(DEMO_ANALYST_ID),
    }
    missing = await api_client.post("/api/measurement/calculate", json=calculation_request)
    assert missing.status_code == 422, missing.text
    async with e2e_context.session_factory() as session:
        evidence = await session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_document_id == UUID(supplier.json()["source_document_id"])
            )
        )
        evidence_id = evidence.id
    payload = {
        "company_id": context["company_id"],
        "actor_id": str(DEMO_ANALYST_ID),
        "metric_definition_id": str(EMISSIONS_METRIC_ID),
        "evidence_item_id": str(evidence_id),
        "factor_code": "NEW-MATERIAL-PCF",
        "version": "1",
        "name": "Synthetic material PCF",
        "material_code": "NEW-MATERIAL",
        "product_code": "NEW-PRODUCT",
        "geography": "GLOBAL",
        "factor_value": "2.5",
        "source_quality": "0.95",
        "factor_specificity": "1",
        "factor_recency": "1",
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
    }
    created = await api_client.post("/api/emission-factors", json=payload)
    assert created.status_code == 201, created.text
    measured = await api_client.post("/api/measurement/calculate", json=calculation_request)
    assert measured.status_code == 200, measured.text
    assert Decimal(measured.json()["value_kgco2e"]) == Decimal(250)
    assert measured.json()["factors"][0]["id"] == created.json()["factor"]["id"]
    lineage = await api_client.get(
        f"/api/measurements/{measured.json()['id']}/lineage",
        params={"company_id": context["company_id"]},
    )
    assert lineage.status_code == 200, lineage.text
    assert any(
        node.get("event_type") == "factor.registered"
        for node in lineage.json()["nodes"]
    )
    lineage = await api_client.get(
        f"/api/measurements/{measured.json()['id']}/lineage",
        params={"company_id": context["company_id"]},
    )
    assert lineage.status_code == 200, lineage.text
    assert any(
        node.get("event_type") == "factor.registered"
        and node["id"] == created.json()["ledger_event_id"]
        for node in lineage.json()["nodes"]
    ), lineage.json()
    second_version = await api_client.post(
        "/api/emission-factors", json={**payload, "version": "2", "factor_value": "2.6"}
    )
    assert second_version.status_code == 201, second_version.text
    ambiguous = await api_client.post("/api/measurement/calculate", json=calculation_request)
    assert ambiguous.status_code == 409, ambiguous.text
    assert ambiguous.json()["detail"]["code"] == "emission_factor_ambiguous"


@pytest.mark.asyncio
async def test_factor_audit_failure_rolls_back_factor_event_and_evidence(
    api_client, e2e_context, monkeypatch
):
    payload = await _factor_request(api_client, e2e_context)
    original_add = FactorRepository.add

    def fail_audit(repository, instance):
        if isinstance(instance, AuditLog) and instance.action == "factor.registered":
            raise SQLAlchemyError("Synthetic audit persistence failure")
        original_add(repository, instance)

    monkeypatch.setattr(FactorRepository, "add", fail_audit)
    response = await api_client.post("/api/emission-factors", json=payload)
    assert response.status_code == 503, response.text
    async with e2e_context.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(EmissionFactor)
                .where(EmissionFactor.factor_code == payload["factor_code"])
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEvent)
                .where(LedgerEvent.event_type == "factor.registered")
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEventEvidence)
                .where(LedgerEventEvidence.evidence_item_id == UUID(payload["evidence_item_id"]))
            )
            == 0
        )
