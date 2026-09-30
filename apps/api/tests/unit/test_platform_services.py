from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.tracing import ensure_trace_id
from app.db.models.core import Actor
from app.db.models.procurement import Approval, Recommendation
from app.modules.approvals import repository as approval_repository
from app.modules.approvals import service as approval_service
from app.modules.approvals.schemas import ApprovalDecisionRequest
from app.modules.approvals.service import (
    ApprovalNotFoundError,
    create_pending_approval,
    decide_approval,
)
from app.modules.integrations.electricity_maps import (
    ElectricityMapsHttpClient,
    ElectricityMapsProviderError,
)
from app.modules.integrations.schemas import GridIntensitySyncRequest
from app.modules.integrations.service import test_electricity_maps as check_electricity_maps
from app.modules.ledger.service import payload_sha256


def test_trace_id_is_preserved_or_generated() -> None:
    assert ensure_trace_id("caller-trace.123") == "caller-trace.123"
    generated = ensure_trace_id(None)
    assert generated
    assert len(generated) <= 100


def test_ledger_payload_hash_is_canonical_and_json_safe() -> None:
    entity_id = UUID("00000000-0000-4000-8000-000000000001")
    timestamp = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

    left, left_hash = payload_sha256({"value": Decimal("10.800"), "id": entity_id, "at": timestamp})
    right, right_hash = payload_sha256(
        {"at": timestamp, "id": entity_id, "value": Decimal("10.800")}
    )

    assert (
        left
        == right
        == {
            "at": "2026-09-30T12:00:00+00:00",
            "id": str(entity_id),
            "value": "10.800",
        }
    )
    assert left_hash == right_hash
    assert len(left_hash) == 64


def test_grid_sync_request_enforces_electricity_maps_hourly_limit() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)

    with pytest.raises(ValidationError, match="cannot exceed 240 hours"):
        GridIntensitySyncRequest(start=start, end=start + timedelta(hours=241))


def test_command_payloads_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        GridIntensitySyncRequest.model_validate({"unexpected": True})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ApprovalDecisionRequest.model_validate(
            {
                "company_id": str(uuid4()),
                "decision": "approve",
                "preview_hash": "a" * 64,
                "actor_id": str(uuid4()),
                "unexpected": True,
            }
        )


def test_approval_decision_requires_company_scope() -> None:
    with pytest.raises(ValidationError, match="company_id"):
        ApprovalDecisionRequest.model_validate(
            {
                "decision": "approve",
                "preview_hash": "a" * 64,
                "actor_id": str(uuid4()),
            }
        )


@pytest.mark.asyncio
async def test_approval_decision_locks_only_the_requested_company(monkeypatch) -> None:
    company_id = uuid4()
    approval_id = uuid4()
    captured: dict[str, UUID] = {}

    async def no_scoped_approval(
        _session,
        *,
        company_id: UUID,
        approval_id: UUID,
    ):
        captured.update(company_id=company_id, approval_id=approval_id)

    monkeypatch.setattr(approval_repository, "get_approval_for_update", no_scoped_approval)
    request = ApprovalDecisionRequest(
        company_id=company_id,
        decision="approve",
        preview_hash="a" * 64,
        actor_id=uuid4(),
    )

    with pytest.raises(ApprovalNotFoundError, match="not found"):
        await decide_approval(
            object(),  # type: ignore[arg-type]
            approval_id=approval_id,
            request=request,
        )

    assert captured == {"company_id": company_id, "approval_id": approval_id}


@pytest.mark.asyncio
async def test_unconfigured_electricity_maps_client_does_not_attempt_network() -> None:
    client = ElectricityMapsHttpClient(token=None)

    with pytest.raises(ElectricityMapsProviderError) as caught:
        await client.list_zones()

    assert caught.value.code == "integration_not_configured"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_token_test_returns_bounded_access_metadata() -> None:
    class FakeProvider:
        async def list_zones(self):
            return {
                "IN": {
                    "zoneKey": "IN",
                    "zoneName": "India",
                    "countryCode": "IN",
                    "access": ["carbon-intensity/past-range"],
                },
                "DE": {
                    "zoneKey": "DE",
                    "zoneName": "Germany",
                    "countryCode": "DE",
                    "access": ["*"],
                },
            }

        async def get_carbon_intensity_range(self, **_):  # pragma: no cover - protocol stub
            raise AssertionError("The token test must not fetch carbon intensity.")

    result = await check_electricity_maps(FakeProvider(), max_zones=1)

    assert result.authenticated is True
    assert result.accessible_zone_count == 2
    assert result.zones_truncated is True
    assert len(result.zones) == 1
    assert "token" not in result.model_dump_json().lower()


@pytest.mark.asyncio
async def test_pending_approval_binds_exact_recommendation_preview(monkeypatch) -> None:
    async def no_existing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(approval_repository, "get_pending_approval", no_existing)
    session = FakeSession()
    company_id = uuid4()
    recommendation = Recommendation(
        id=uuid4(),
        company_id=company_id,
        scenario_id=uuid4(),
        recommended_product_id=uuid4(),
        baseline_product_id=uuid4(),
        supplier_score_id=uuid4(),
        status="pending_approval",
        projected_footprint_kgco2e=Decimal(22800),
        avoided_kgco2e=Decimal(10800),
        reduction_pct=Decimal("32.1"),
        cost_delta_pct=Decimal("3.2"),
        lead_time_delta_days=2,
        rationale_template="template",
        analysis_signature="a" * 64,
        payload_hash="b" * 64,
        impact_snapshot={},
    )

    approval = await create_pending_approval(
        session,
        recommendation=recommendation,
        requested_by=uuid4(),
    )

    assert approval.preview_hash == recommendation.payload_hash
    assert approval.analysis_signature == recommendation.analysis_signature
    assert approval.status == "pending"
    assert approval in session.added
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_approval_list_bulk_loads_preview_integrity_once(monkeypatch) -> None:
    company_id = uuid4()
    requester = Actor(
        id=uuid4(),
        company_id=company_id,
        email="requester@example.test",
        display_name="Requestor",
        role="procurement_manager",
        is_active=True,
    )
    now = datetime.now(UTC)
    records = []
    recommendations = []
    for index in range(2):
        recommendation = Recommendation(
            id=uuid4(),
            company_id=company_id,
            scenario_id=uuid4(),
            recommended_product_id=uuid4(),
            baseline_product_id=uuid4(),
            supplier_score_id=uuid4(),
            status="pending_approval",
            projected_footprint_kgco2e=Decimal(22800),
            avoided_kgco2e=Decimal(10800),
            reduction_pct=Decimal("32.1"),
            cost_delta_pct=Decimal("3.2"),
            lead_time_delta_days=2,
            rationale_template="template",
            analysis_signature="a" * 64,
            payload_hash=(f"{index + 1:x}" * 64)[:64],
            impact_snapshot={
                "review": {
                    "recommended_product": {
                        "name": f"Product {index}",
                        "supplier_name": "Supplier",
                    },
                    "supplier_score": {"impact": {}},
                }
            },
        )
        approval = Approval(
            id=uuid4(),
            company_id=company_id,
            recommendation_id=recommendation.id,
            requested_by=requester.id,
            status="pending",
            preview_hash=recommendation.payload_hash,
            analysis_signature=recommendation.analysis_signature,
            idempotency_key=f"approval-{index}",
            expires_at=now + timedelta(hours=1),
            created_at=now,
        )
        recommendations.append(recommendation)
        records.append((approval, recommendation, requester, None))

    async def list_records(*_args, **_kwargs):
        return records, len(records)

    bulk_calls: list[list[UUID]] = []

    async def load_bulk_inputs(_session, *, company_id: UUID, recommendations):
        assert company_id == requester.company_id
        bulk_calls.append([item.id for item in recommendations])
        return {}

    def bulk_statuses(recommendations, inputs):
        assert inputs == {}
        return {
            recommendation.id: recommendation is records[0][1] for recommendation in recommendations
        }

    async def per_row_check_must_not_run(*_args, **_kwargs):
        raise AssertionError("approval listing must not run per-row integrity queries")

    monkeypatch.setattr(approval_repository, "list_approval_records", list_records)
    monkeypatch.setattr(
        approval_repository,
        "load_recommendation_review_inputs",
        load_bulk_inputs,
    )
    monkeypatch.setattr(approval_service, "recommendation_previews_are_current", bulk_statuses)
    monkeypatch.setattr(
        approval_service,
        "recommendation_preview_is_current",
        per_row_check_must_not_run,
    )

    result = await approval_service.list_approvals(
        object(),  # type: ignore[arg-type]
        company_id=company_id,
        status=None,
        limit=50,
        offset=0,
    )

    assert bulk_calls == [[item.id for item in recommendations]]
    assert [item.preview_current for item in result.items] == [True, False]


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_count = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1
