"""Real API identity isolation, cookie SSE compatibility, and approval authorization."""

import json
from uuid import UUID, uuid4

import pytest
from itsdangerous import TimestampSigner
from sqlalchemy import select

from app.core.auth import issue_token
from app.core.config import get_settings
from app.db.models.core import AuditLog, EvidenceItem
from app.db.models.ledger import LedgerEvent
from app.modules.demo.fixtures import ACTIVITY_METRIC_ID, EMISSIONS_METRIC_ID
from app.modules.demo.service import reset_and_seed_demo

ANALYST_KEY = "synthetic-analyst-access-key-at-least-32"
APPROVER_KEY = "synthetic-approver-access-key-at-least-32"


@pytest.fixture
def authentication(monkeypatch, e2e_context):
    ids = e2e_context.ids
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_SIGNING_KEY", "synthetic-signing-key-never-use-in-production")
    monkeypatch.setenv("AUTH_ACCESS_KEYS", json.dumps([
        {"key": ANALYST_KEY, "company_id": str(ids.company_id), "actor_id": str(ids.analyst_id)},
        {"key": APPROVER_KEY, "company_id": str(ids.company_id), "actor_id": str(ids.approver_id)},
    ]))
    return ids


@pytest.mark.asyncio
async def test_authentication_binds_tenant_and_actor(api_client, authentication):
    ids = authentication
    url = "/api/ledger/events"
    assert (await api_client.get(url, params={"company_id": str(ids.company_id)})).status_code == 401
    denied = await api_client.post("/api/auth/session", json={"access_key": "wrong"})
    assert denied.status_code == 401
    logged_in = await api_client.post("/api/auth/session", json={"access_key": ANALYST_KEY})
    assert logged_in.status_code == 200, logged_in.text
    assert ANALYST_KEY not in logged_in.text
    assert "httponly" in logged_in.headers["set-cookie"].lower()
    assert (await api_client.get("/api/auth/session")).json()["actor_id"] == str(ids.analyst_id)
    assert (await api_client.get(url, params={"company_id": str(ids.company_id)})).status_code == 200
    assert (await api_client.get(url, params={"company_id": str(uuid4())})).status_code == 403
    forged_actor = await api_client.post("/api/agent/requests", json={
        "query": "measure Plant B", "context": {
            "company_id": str(ids.company_id), "actor_id": str(ids.approver_id),
        },
    })
    assert forged_actor.status_code == 403
    assert forged_actor.json()["detail"]["code"] == "identity_mismatch"
    assert (await api_client.post(f"/api/approvals/{uuid4()}/decision", json={})).status_code == 403
    assert (await api_client.delete("/api/auth/session")).status_code == 204
    assert (await api_client.get(url, params={"company_id": str(ids.company_id)})).status_code == 401


@pytest.mark.asyncio
async def test_bearer_token_tampering_expiry_and_grant_revocation(
    api_client, authentication, monkeypatch,
):
    logged_in = await api_client.post("/api/auth/session", json={"access_key": APPROVER_KEY})
    token = logged_in.json()["access_token"]
    api_client.cookies.clear()
    assert (await api_client.get("/api/auth/session", headers={
        "Authorization": f"Bearer {token}",
    })).status_code == 200
    assert (await api_client.get("/api/auth/session", headers={
        "Authorization": f"Bearer {token}tampered",
    })).status_code == 401
    from app.core.auth import Principal

    principal = Principal.model_validate(logged_in.json()["principal"])
    original_clock = TimestampSigner.get_timestamp
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: original_clock(self) - 7200)
    expired = issue_token(principal, get_settings())
    monkeypatch.setattr(TimestampSigner, "get_timestamp", original_clock)
    assert (await api_client.get("/api/auth/session", headers={
        "Authorization": f"Bearer {expired}",
    })).status_code == 401
    monkeypatch.setenv("AUTH_ACCESS_KEYS", json.dumps([{
        "key": ANALYST_KEY, "company_id": str(authentication.company_id),
        "actor_id": str(authentication.analyst_id),
    }]))
    assert (await api_client.get("/api/auth/session", headers={
        "Authorization": f"Bearer {token}",
    })).status_code == 401


@pytest.mark.asyncio
async def test_cross_origin_cookie_mutations_are_rejected(api_client, authentication):
    response = await api_client.post("/api/auth/session", json={"access_key": ANALYST_KEY},
                                     headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    assert (await api_client.post("/api/auth/session", json={"access_key": ANALYST_KEY})).status_code == 200
    assert (await api_client.delete("/api/auth/session", headers={
        "Origin": "https://untrusted.example",
    })).status_code == 403


@pytest.mark.asyncio
async def test_disabled_actor_revokes_an_existing_session(
    api_client, authentication, e2e_context,
):
    from sqlalchemy import update

    from app.db.models.core import Actor

    response = await api_client.post("/api/auth/session", json={"access_key": ANALYST_KEY})
    assert response.status_code == 200
    async with e2e_context.session_factory() as session:
        await session.execute(update(Actor).where(Actor.id == authentication.analyst_id)
                              .values(is_active=False))
        await session.commit()
    revoked = await api_client.get("/api/auth/session")
    assert revoked.status_code == 401
    assert revoked.json()["detail"]["code"] == "session_revoked"


@pytest.mark.asyncio
async def test_authenticated_commands_bind_omitted_actor_to_persisted_audit(
    api_client, authentication, e2e_context,
):
    ids = authentication
    # A disposable canonical foundation has the currently versioned calculation methods.
    async with e2e_context.session_factory() as session:
        await reset_and_seed_demo(session)
    login = await api_client.post("/api/auth/session", json={"access_key": ANALYST_KEY})
    assert login.status_code == 200, login.text
    uploaded = await api_client.post("/api/sources/upload", json={
        "company_id": str(ids.company_id), "site_id": str(ids.site_id),
        "reporting_period_id": str(ids.reporting_period_id),
        "source_name": "Synthetic authenticated source", "filename": "identity.txt",
        "content_type": "text/plain", "source_type": "synthetic", "is_synthetic": True,
        "content": "Synthetic source uploaded with server-bound analyst identity.",
        "evidence_type": "disclosure_support",
    })
    assert uploaded.status_code == 201, uploaded.text
    document_id = UUID(uploaded.json()["document"]["id"])
    async with e2e_context.session_factory() as session:
        audit = await session.scalar(select(AuditLog).where(
            AuditLog.entity_id == document_id, AuditLog.action == "source.uploaded",
        ))
        evidence = await session.scalar(select(EvidenceItem).where(EvidenceItem.source_document_id == document_id))
        assert audit.actor_id == ids.analyst_id
        assert evidence.evidence_metadata["actor_id"] == str(ids.analyst_id)

    calculated = await api_client.post("/api/measurement/calculate", json={
        "company_id": str(ids.company_id), "site_id": str(ids.site_id),
        "reporting_period_id": str(ids.reporting_period_id),
        "material_code": "RECYCLED-ALUMINIUM",
    })
    assert calculated.status_code == 200, calculated.text
    async with e2e_context.session_factory() as session:
        event = await session.get(LedgerEvent, UUID(calculated.json()["facts"]["ledger_event_id"]))
        assert event.created_by == ids.analyst_id
        audit = await session.scalar(select(AuditLog).where(
            AuditLog.entity_id == UUID(calculated.json()["id"]), AuditLog.action == "measurement.calculated",
        ))
        assert audit.actor_id == ids.analyst_id

    resolved = await api_client.post("/api/context/resolve", json={
        "company_id": str(ids.company_id), "site_id": str(ids.site_id),
        "reporting_period_id": str(ids.reporting_period_id),
        "metric_definition_ids": [str(EMISSIONS_METRIC_ID)], "workflow": "measurement",
    })
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["actor"]["id"] == str(ids.analyst_id)

    # Even rejected imports retain an attributed source/issue/audit transaction.
    for path, extra in (
        ("/api/imports/suppliers", {}),
        ("/api/activities/import", {"site_id": str(ids.site_id),
            "reporting_period_id": str(ids.reporting_period_id),
            "metric_definition_id": str(ACTIVITY_METRIC_ID)}),
    ):
        imported = await api_client.post(path, json={
            "company_id": str(ids.company_id), "source_name": f"Synthetic attributed {path}",
            "filename": "empty.json", "content_type": "application/json",
            "content": [], "is_synthetic": True, **extra,
        })
        assert imported.status_code == 201, imported.text
        async with e2e_context.session_factory() as session:
            audit = await session.scalar(select(AuditLog).where(
                AuditLog.entity_id == UUID(imported.json()["import_id"]),
                AuditLog.action.in_(("import.completed", "import.failed")),
            ))
            assert audit.actor_id == ids.analyst_id
