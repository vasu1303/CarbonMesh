from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.api.routes import ledger as ledger_routes
from app.db.models.core import DataSource, EvidenceItem, SourceDocument
from app.db.models.ledger import LedgerEvent, LedgerEventEvidence, LineageEdge
from app.dependencies.database import get_db_session
from app.modules.ledger import repository
from app.modules.ledger.schemas import LedgerEventListResult
from app.modules.ledger.service import (
    InvalidLedgerQueryError,
    LedgerEventNotFoundError,
    get_ledger_event_detail,
    search_ledger_events,
)

COMPANY_ID = UUID("00000000-0000-4000-8000-000000000001")
EVENT_ID = UUID("00000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _event(
    *,
    event_id: UUID = EVENT_ID,
    event_type: str = "measurement.verified",
    entity_type: str = "carbon_measurement",
) -> LedgerEvent:
    return LedgerEvent(
        id=event_id,
        company_id=COMPANY_ID,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=uuid4(),
        payload={"value_kgco2e": "33600.000000"},
        payload_hash="a" * 64,
        analysis_signature="b" * 64,
        created_by=None,
        supersedes_event_id=None,
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_search_normalizes_filters_and_forwards_tenant_scope(monkeypatch) -> None:
    captured: dict[str, object] = {}
    event = _event()

    async def fake_search(_session, **kwargs):
        captured.update(kwargs)
        return [event], 1

    monkeypatch.setattr(repository, "search_ledger_events", fake_search)
    local_zone = timezone(timedelta(hours=5, minutes=30))

    result = await search_ledger_events(
        object(),  # type: ignore[arg-type]
        company_id=COMPANY_ID,
        event_type="  measurement.verified ",
        entity_type=" carbon_measurement ",
        entity_id=event.entity_id,
        agent_run_id=uuid4(),
        created_from=datetime(2026, 10, 1, 17, 30, tzinfo=local_zone),
        created_to=datetime(2026, 10, 1, 18, 30, tzinfo=local_zone),
        limit=25,
        offset=5,
    )

    assert result.total == 1
    assert result.items[0].id == EVENT_ID
    assert "payload" not in result.items[0].model_dump()
    assert captured["company_id"] == COMPANY_ID
    assert captured["event_type"] == "measurement.verified"
    assert captured["entity_type"] == "carbon_measurement"
    assert captured["created_from"] == NOW
    assert captured["created_to"] == NOW + timedelta(hours=1)
    assert captured["limit"] == 25
    assert captured["offset"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"limit": 101}, "limit"),
        ({"offset": 10_001}, "offset"),
        ({"event_type": "   "}, "event_type"),
        ({"created_from": NOW.replace(tzinfo=None)}, "created_from"),
        (
            {
                "created_from": NOW,
                "created_to": NOW - timedelta(seconds=1),
            },
            "created_to",
        ),
    ],
)
async def test_search_rejects_unbounded_or_invalid_filters(
    kwargs: dict[str, object],
    field: str,
) -> None:
    with pytest.raises(InvalidLedgerQueryError) as caught:
        await search_ledger_events(
            object(),  # type: ignore[arg-type]
            company_id=COMPANY_ID,
            **kwargs,  # type: ignore[arg-type]
        )

    assert caught.value.field == field


@pytest.mark.asyncio
async def test_event_detail_returns_safe_evidence_and_immediate_neighbors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _event()
    evidence = EvidenceItem(
        id=uuid4(),
        company_id=COMPANY_ID,
        source_document_id=uuid4(),
        evidence_type="emission_factor",
        locator="page:1",
        content_text="secret raw evidence body",
        checksum="c" * 64,
        evidence_metadata={"quality": "primary"},
        created_at=NOW,
    )
    document = SourceDocument(
        id=evidence.source_document_id,
        company_id=COMPANY_ID,
        data_source_id=uuid4(),
        filename="synthetic-factor.pdf",
        content_type="application/pdf",
        checksum="d" * 64,
    )
    data_source = DataSource(
        id=document.data_source_id,
        company_id=COMPANY_ID,
        name="Synthetic factors",
        source_type="synthetic",
        status="ready",
        is_synthetic=True,
    )
    link = LedgerEventEvidence(
        company_id=COMPANY_ID,
        ledger_event_id=event.id,
        evidence_item_id=evidence.id,
        relevance="Supports the selected factor",
        created_at=NOW,
    )
    parent = _event(event_id=uuid4(), event_type="measurement.factor_used")
    child = _event(event_id=uuid4(), event_type="recommendation.created")
    parent_edge = LineageEdge(
        id=uuid4(),
        company_id=COMPANY_ID,
        parent_event_id=parent.id,
        child_event_id=event.id,
        relationship_type="used_emission_factor",
        edge_metadata={"role": "factor"},
        created_at=NOW,
    )
    child_edge = LineageEdge(
        id=uuid4(),
        company_id=COMPANY_ID,
        parent_event_id=event.id,
        child_event_id=child.id,
        relationship_type="informed_recommendation",
        edge_metadata={},
        created_at=NOW,
    )
    calls: list[tuple[str, UUID, UUID, int | None]] = []

    async def fake_get(_session, *, company_id, event_id):
        calls.append(("event", company_id, event_id, None))
        return event

    async def fake_evidence(_session, *, company_id, event_id, limit):
        calls.append(("evidence", company_id, event_id, limit))
        return [(link, evidence, document, data_source)]

    async def fake_parents(_session, *, company_id, event_id, limit):
        calls.append(("parents", company_id, event_id, limit))
        return [(parent_edge, parent)]

    async def fake_children(_session, *, company_id, event_id, limit):
        calls.append(("children", company_id, event_id, limit))
        return [(child_edge, child)]

    monkeypatch.setattr(repository, "get_ledger_event", fake_get)
    monkeypatch.setattr(repository, "list_bounded_event_evidence", fake_evidence)
    monkeypatch.setattr(repository, "list_parent_neighbors", fake_parents)
    monkeypatch.setattr(repository, "list_child_neighbors", fake_children)

    result = await get_ledger_event_detail(
        object(),  # type: ignore[arg-type]
        company_id=COMPANY_ID,
        event_id=EVENT_ID,
    )

    assert result.payload == {"value_kgco2e": "33600.000000"}
    assert result.evidence[0].source_filename == "synthetic-factor.pdf"
    assert result.evidence[0].relevance == "Supports the selected factor"
    assert result.parents[0].event.id == parent.id
    assert result.children[0].event.id == child.id
    assert "secret raw evidence body" not in result.model_dump_json()
    assert {call[1] for call in calls} == {COMPANY_ID}
    assert {call[2] for call in calls} == {EVENT_ID}
    assert {call[3] for call in calls if call[3] is not None} == {101}


@pytest.mark.asyncio
async def test_event_detail_hides_cross_tenant_event_as_not_found(monkeypatch) -> None:
    async def no_event(_session, *, company_id, event_id):
        assert company_id == COMPANY_ID
        assert event_id == EVENT_ID

    monkeypatch.setattr(repository, "get_ledger_event", no_event)

    with pytest.raises(LedgerEventNotFoundError):
        await get_ledger_event_detail(
            object(),  # type: ignore[arg-type]
            company_id=COMPANY_ID,
            event_id=EVENT_ID,
        )


class RecordingSession:
    def __init__(self) -> None:
        self.statements = []

    async def scalar(self, statement):
        self.statements.append(statement)
        return 0

    async def scalars(self, statement):
        self.statements.append(statement)
        return []


@pytest.mark.asyncio
async def test_repository_search_scopes_agent_filter_through_structured_links() -> None:
    session = RecordingSession()
    agent_run_id = uuid4()

    await repository.search_ledger_events(
        session,  # type: ignore[arg-type]
        company_id=COMPANY_ID,
        event_type="measurement.verified",
        entity_type="carbon_measurement",
        entity_id=uuid4(),
        agent_run_id=agent_run_id,
        created_from=NOW - timedelta(days=1),
        created_to=NOW,
        limit=20,
        offset=10,
    )

    query = session.statements[1].compile(dialect=postgresql.dialect())
    sql = str(query)
    assert "ledger.ledger_events.company_id" in sql
    assert "core.audit_log" in sql
    assert "ledger.fact_bindings" in sql
    assert "ledger.ledger_events.created_at DESC" in sql
    assert COMPANY_ID in query.params.values()
    assert agent_run_id in query.params.values()


def _client() -> TestClient:
    application = FastAPI()
    application.include_router(ledger_routes.router, prefix="/api")
    application.dependency_overrides[get_db_session] = lambda: object()
    return TestClient(application)


def test_list_route_forwards_all_bounded_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_search(_session, **kwargs):
        captured.update(kwargs)
        return LedgerEventListResult(items=[], total=0, limit=25, offset=5)

    monkeypatch.setattr(ledger_routes, "search_ledger_events", fake_search)
    entity_id = uuid4()
    agent_run_id = uuid4()
    response = _client().get(
        "/api/ledger/events",
        params={
            "company_id": str(COMPANY_ID),
            "event_type": "measurement.verified",
            "entity_type": "carbon_measurement",
            "entity_id": str(entity_id),
            "agent_run_id": str(agent_run_id),
            "created_from": "2026-10-01T11:00:00Z",
            "created_to": "2026-10-01T12:00:00Z",
            "limit": 25,
            "offset": 5,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 25, "offset": 5}
    assert captured["company_id"] == COMPANY_ID
    assert captured["entity_id"] == entity_id
    assert captured["agent_run_id"] == agent_run_id
    assert captured["limit"] == 25
    assert captured["offset"] == 5


def test_ledger_routes_require_tenant_and_bound_pagination() -> None:
    client = _client()
    missing_company = client.get("/api/ledger/events")
    operation = client.app.openapi()["paths"]["/api/ledger/events"]["get"]
    parameters = {
        (parameter["in"], parameter["name"]): parameter
        for parameter in operation["parameters"]
    }

    assert missing_company.status_code == 422
    assert parameters[("query", "company_id")]["required"] is True
    assert parameters[("query", "limit")]["schema"]["maximum"] == 100
    assert parameters[("query", "offset")]["schema"]["maximum"] == 10_000


def test_detail_route_returns_tenant_safe_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def not_found(_session, **_kwargs):
        raise LedgerEventNotFoundError

    monkeypatch.setattr(ledger_routes, "get_ledger_event_detail", not_found)
    response = _client().get(
        f"/api/ledger/events/{EVENT_ID}",
        params={"company_id": str(COMPANY_ID)},
    )

    assert response.status_code == 404
    body = response.json()["detail"]
    assert body["code"] == "ledger_event_not_found"
    assert body["retryable"] is False
    assert body["field_details"] == [
        {"field": "event_id", "detail": str(EVENT_ID)}
    ]
