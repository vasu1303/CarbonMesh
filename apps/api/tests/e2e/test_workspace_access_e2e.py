"""Credential-free demo access keeps explicit actor attribution and tenant scope."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from app.db.models.core import Actor, AuditLog, Company, DataSource, EvidenceItem
from app.db.models.ledger import LedgerEvent
from app.db.models.semantic import MetricDefinition
from app.modules.demo.fixtures import EMISSIONS_METRIC_ID
from app.modules.demo.service import reset_and_seed_demo


@pytest.mark.asyncio
async def test_reads_need_no_session_and_keep_explicit_tenant_scope(
    api_client, e2e_context, monkeypatch,
):
    ids = e2e_context.ids
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    params = {"company_id": str(ids.company_id)}
    for path in ("/api/ledger/events", "/api/measurements", "/api/approvals"):
        response = await api_client.get(path, params=params)
        assert response.status_code == 200, response.text
        assert "set-cookie" not in response.headers

    path = f"/api/measurements/{ids.measurement_id}"
    legacy_credentials = await api_client.get(
        path,
        params=params,
        headers={
            "Authorization": "Bearer obsolete-synthetic-token",
            "Cookie": "carbonmesh_session=obsolete-synthetic-session",
        },
    )
    assert legacy_credentials.status_code == 200, legacy_credentials.text
    assert (await api_client.get(
        path, params={"company_id": str(uuid4())},
    )).status_code == 404


@pytest.mark.asyncio
async def test_source_upload_records_the_explicit_actor_without_a_session(
    api_client, e2e_context,
):
    ids = e2e_context.ids
    uploaded = await api_client.post("/api/sources/upload", json={
        "company_id": str(ids.company_id), "site_id": str(ids.site_id),
        "reporting_period_id": str(ids.reporting_period_id),
        "actor_id": str(ids.analyst_id),
        "source_name": "Synthetic attributed source", "filename": "identity.txt",
        "content_type": "text/plain", "source_type": "synthetic", "is_synthetic": True,
        "content": "Synthetic source uploaded with the explicitly selected analyst.",
        "evidence_type": "disclosure_support",
    })
    assert uploaded.status_code == 201, uploaded.text
    assert "set-cookie" not in uploaded.headers
    document_id = UUID(uploaded.json()["document"]["id"])
    async with e2e_context.session_factory() as session:
        audit = await session.scalar(select(AuditLog).where(
            AuditLog.entity_id == document_id, AuditLog.action == "source.uploaded",
        ))
        evidence = await session.scalar(select(EvidenceItem).where(
            EvidenceItem.source_document_id == document_id,
        ))
        assert audit.actor_id == ids.analyst_id
        assert evidence.evidence_metadata["actor_id"] == str(ids.analyst_id)


@pytest.mark.asyncio
async def test_measurement_and_context_keep_the_explicit_actor_without_a_session(
    api_client, e2e_context,
):
    ids = e2e_context.ids
    # Only the disposable E2E database is reset to the current canonical methods.
    async with e2e_context.session_factory() as session:
        await reset_and_seed_demo(session)
    context = {
        "company_id": str(ids.company_id), "site_id": str(ids.site_id),
        "reporting_period_id": str(ids.reporting_period_id),
        "actor_id": str(ids.analyst_id),
    }
    calculated = await api_client.post("/api/measurement/calculate", json={
        **context, "material_code": "RECYCLED-ALUMINIUM",
    })
    assert calculated.status_code == 200, calculated.text
    async with e2e_context.session_factory() as session:
        event = await session.get(LedgerEvent, UUID(calculated.json()["facts"]["ledger_event_id"]))
        assert event.created_by == ids.analyst_id
        audit = await session.scalar(select(AuditLog).where(
            AuditLog.entity_id == UUID(calculated.json()["id"]),
            AuditLog.action == "measurement.calculated",
        ))
        assert audit.actor_id == ids.analyst_id

    resolved = await api_client.post("/api/context/resolve", json={
        **context,
        "metric_definition_ids": [str(EMISSIONS_METRIC_ID)], "workflow": "measurement",
    })
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["actor"]["id"] == str(ids.analyst_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/activities/import", "/api/imports/suppliers"])
async def test_imports_validate_and_preserve_explicit_actors_during_replay(
    api_client, e2e_context, path,
):
    ids = e2e_context.ids
    foreign_company, foreign_actor, inactive_actor = uuid4(), uuid4(), uuid4()
    async with e2e_context.session_factory() as session, session.begin():
        session.add(Company(
            id=foreign_company, code="OTHER-SYNTHETIC", name="Other synthetic company",
            is_synthetic=True,
        ))
        await session.flush()
        session.add_all([
            Actor(
                id=foreign_actor, company_id=foreign_company,
                email="analyst@other.example", display_name="Other synthetic analyst",
                role="sustainability_analyst", is_active=True,
            ),
            Actor(
                id=inactive_actor, company_id=ids.company_id,
                email="inactive@synthetic.example", display_name="Inactive synthetic analyst",
                role="sustainability_analyst", is_active=False,
            ),
        ])
        metric_id = await session.scalar(select(MetricDefinition.id).where(
            MetricDefinition.company_id == ids.company_id,
            MetricDefinition.key == "activity.electricity_consumption",
        ))
    request = {
        "company_id": str(ids.company_id), "actor_id": str(ids.analyst_id),
        "source_name": "Synthetic attributed import", "filename": "empty.json",
        "content_type": "application/json", "content": [], "is_synthetic": True,
        "idempotency_key": "synthetic-attributed-import",
    }
    if path == "/api/activities/import":
        request.update({
            "site_id": str(ids.site_id), "reporting_period_id": str(ids.reporting_period_id),
            "metric_definition_id": str(metric_id),
        })

    for invalid_actor in (uuid4(), foreign_actor, inactive_actor):
        rejected = await api_client.post(path, json={**request, "actor_id": str(invalid_actor)})
        assert rejected.status_code == 404, rejected.text
        assert rejected.json()["detail"]["code"] == "reference_not_found"
    async with e2e_context.session_factory() as session:
        count = await session.scalar(select(func.count(DataSource.id)).where(
            DataSource.configuration["idempotency_key"].astext == request["idempotency_key"],
        ))
        assert count == 0

    # Rejected source rows still create an attributed import and audit transaction.
    imported = await api_client.post(path, json=request)
    assert imported.status_code == 201, imported.text
    replay = await api_client.post(path, json=request)
    assert replay.status_code == 201, replay.text
    assert replay.json() == imported.json()
    changed_actor = await api_client.post(path, json={
        **request, "actor_id": str(ids.procurement_manager_id),
    })
    assert changed_actor.status_code == 409, changed_actor.text
    assert changed_actor.json()["detail"]["code"] == "import_idempotency_conflict"

    async with e2e_context.session_factory() as session, session.begin():
        audits = (await session.scalars(select(AuditLog).where(
            AuditLog.entity_id == UUID(imported.json()["import_id"]),
            AuditLog.action.in_(("import.completed", "import.failed")),
        ))).all()
        assert len(audits) == 1
        assert audits[0].actor_id == ids.analyst_id
        await session.execute(update(Actor).where(Actor.id == ids.analyst_id).values(is_active=False))
    inactive_replay = await api_client.post(path, json=request)
    assert inactive_replay.status_code == 404, inactive_replay.text
    assert inactive_replay.json()["detail"]["code"] == "reference_not_found"
