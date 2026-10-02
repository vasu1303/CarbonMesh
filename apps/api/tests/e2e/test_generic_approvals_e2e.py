"""Synthetic, disposable-database approval journeys across all three domains."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from app.db.models.assurance import DisclosureDraft, DisclosureRequirement
from app.db.models.core import Approval, EvidenceItem
from app.db.models.dispatch import DispatchRecommendation, OperatingConstraint
from app.db.models.ledger import FactBinding, LedgerEvent, LineageEdge
from app.db.models.semantic import MethodDefinition
from app.modules.ledger.service import payload_sha256


async def _dispatch_preview(client, ids):
    sync = await client.post(
        "/api/dispatch/forecasts/sync",
        json={"company_id": str(ids.company_id), "site_id": str(ids.site_id)},
    )
    assert sync.status_code == 200, sync.text
    scenario = await client.post(
        "/api/dispatch/scenarios",
        json={
            "company_id": str(ids.company_id),
            "site_id": str(ids.site_id),
            "flexible_load_id": str(ids.flexible_load_id),
            "method_definition_id": str(ids.dispatch_method_id),
            "policy_definition_id": str(ids.dispatch_policy_id),
            "forecast_source_document_id": sync.json()["source_document_id"],
            "requested_by": str(ids.analyst_id),
            "window_start": "2026-10-01T08:00:00Z",
            "window_end": "2026-10-01T20:00:00Z",
            "baseline_start": "2026-10-01T08:00:00Z",
            "maximum_delay_minutes": 240,
        },
    )
    assert scenario.status_code == 201, scenario.text
    optimized = await client.post(
        f"/api/dispatch/scenarios/{scenario.json()['id']}/optimize",
        json={"company_id": str(ids.company_id)},
    )
    assert optimized.status_code == 200, optimized.text
    return optimized.json()["recommendation"]["approval"]


async def _disclosure_preview(client, context):
    ids = context.ids
    # An alternative synthetic template contains only supportable requirements;
    # the default demo's unsupported prior-period claim remains a separate gate.
    async with context.session_factory() as session, session.begin():
        requirement = await session.scalar(
            select(DisclosureRequirement).where(
                DisclosureRequirement.company_id == ids.company_id,
                DisclosureRequirement.requirement_code == "S2-PRIOR-PERIOD",
            )
        )
        requirement.is_active = False
    created = await client.post(
        "/api/assurance/drafts",
        json={
            "company_id": str(ids.company_id),
            "standard_id": str(ids.assurance_standard_id),
            "site_id": str(ids.site_id),
            "reporting_period_id": str(ids.reporting_period_id),
            "measurement_id": str(ids.measurement_id),
            "requested_by": str(ids.analyst_id),
            "idempotency_key": "supported-synthetic-disclosure",
            "title": "Supported synthetic disclosure",
        },
    )
    assert created.status_code == 201, created.text
    validated = await client.post(
        f"/api/assurance/drafts/{created.json()['id']}/validate",
        json={
            "company_id": str(ids.company_id),
            "requested_by": str(ids.analyst_id),
            "idempotency_key": "validate-supported-synthetic-disclosure",
        },
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["terminal_state"] == "approval_required", validated.text
    return validated.json()["draft"]["approval"]


async def _procurement_preview(client, ids):
    result = await client.post(
        "/api/procurement/scenarios",
        json={
            "company_id": str(ids.company_id),
            "site_id": str(ids.site_id),
            "reporting_period_id": str(ids.reporting_period_id),
            "current_product_id": str(ids.current_product_id),
            "carbon_measurement_id": str(ids.measurement_id),
            "method_definition_id": str(ids.scoring_method_id),
            "requested_by": str(ids.procurement_manager_id),
            "quantity": "10000",
            "quantity_unit": "kg",
            "max_cost_increase_pct": "5",
            "max_lead_time_days": 20,
            "minimum_circularity_score": "50",
        },
    )
    assert result.status_code == 201, result.text
    return result.json()["selected_recommendation"]["approval"]


@pytest.mark.asyncio
async def test_three_domain_queue_exact_details_and_atomic_idempotent_decisions(
    api_client, e2e_context
):
    ids = e2e_context.ids
    previews = [
        await _procurement_preview(api_client, ids),
        await _disclosure_preview(api_client, e2e_context),
        await _dispatch_preview(api_client, ids),
    ]
    params = {"company_id": str(ids.company_id)}
    queue = await api_client.get("/api/approvals", params=params)
    assert queue.status_code == 200, queue.text
    assert queue.json()["total"] == len(queue.json()["items"]) == 3
    assert {item["target_type"] for item in queue.json()["items"]} == {
        "procurement_recommendation",
        "disclosure_draft",
        "dispatch_recommendation",
    }
    assert all(item["preview_current"] for item in queue.json()["items"]), queue.text
    for index, preview in enumerate(previews):
        approval_id = preview["id"]
        detail = await api_client.get(f"/api/approvals/{approval_id}", params=params)
        assert detail.status_code == 200, detail.text
        reviewed = detail.json()
        assert reviewed["preview_current"] is True
        assert payload_sha256(reviewed["preview_payload"])[1] == reviewed["preview_hash"]
        other_tenant = await api_client.get(
            f"/api/approvals/{approval_id}", params={"company_id": str(uuid4())}
        )
        assert other_tenant.status_code == 404
        payload = {
            **params,
            "decision": "reject" if index == 1 else "approve",
            "preview_hash": preview["preview_hash"],
            "actor_id": str(ids.approver_id),
            "decision_note": "Synthetic reviewed exact payload.",
            "idempotency_key": reviewed["idempotency_key"],
        }
        denied = await api_client.post(
            f"/api/approvals/{approval_id}/decision",
            json={**payload, "actor_id": str(ids.analyst_id)},
        )
        assert denied.status_code == 403
        decisions = await asyncio.gather(
            *[
                api_client.post(f"/api/approvals/{approval_id}/decision", json=payload)
                for _ in range(2)
            ]
        )
        assert all(result.status_code == 200 for result in decisions), [
            result.text for result in decisions
        ]
        assert sorted(result.json()["idempotent_replay"] for result in decisions) == [False, True]
        assert len({result.json()["ledger_event_id"] for result in decisions}) == 1
        assert decisions[0].json()["target_type"] == reviewed["target_type"]
        changed_note = await api_client.post(
            f"/api/approvals/{approval_id}/decision",
            json={**payload, "decision_note": "Different decision payload"},
        )
        assert changed_note.status_code == 409
        async with e2e_context.session_factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(LedgerEvent)
                    .where(
                        LedgerEvent.entity_id == UUID(approval_id),
                        LedgerEvent.event_type.in_(["approval.approved", "approval.rejected"]),
                    )
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(LineageEdge)
                    .where(
                        LineageEdge.child_event_id == UUID(decisions[0].json()["ledger_event_id"])
                    )
                )
                == 1
            )
    async with e2e_context.session_factory() as session:
        dispatch = await session.scalar(select(DispatchRecommendation))
        assert dispatch.status == "approved"
        assert dispatch.actuation_authorized is False
        disclosure = await session.scalar(select(DisclosureDraft))
        assert disclosure.status == "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["constraint", "method", "evidence", "binding", "payload", "expiry"]
)
async def test_dispatch_approval_rejects_changed_upstream_state(api_client, e2e_context, changed):
    ids = e2e_context.ids
    preview = await _dispatch_preview(api_client, ids)
    async with e2e_context.session_factory() as session, session.begin():
        approval = await session.get(Approval, UUID(preview["id"]))
        if changed == "constraint":
            constraint = await session.scalar(
                select(OperatingConstraint).where(
                    OperatingConstraint.flexible_load_id == ids.flexible_load_id,
                    OperatingConstraint.constraint_type == "deadline",
                )
            )
            constraint.configuration = {**constraint.configuration, "maximum_delay_minutes": 60}
        elif changed == "method":
            method = await session.get(MethodDefinition, ids.dispatch_method_id)
            method.is_active = False
        elif changed == "evidence":
            evidence_id = approval.preview_payload["forecast_points"][0]["evidence_item_id"]
            evidence = await session.get(EvidenceItem, UUID(evidence_id))
            evidence.checksum = "e" * 64
        elif changed == "payload":
            recommendation = await session.get(DispatchRecommendation, approval.target_id)
            recommendation.expected_emissions_kgco2e += Decimal(1)
        elif changed == "binding":
            binding = await session.scalar(
                select(FactBinding).where(
                    FactBinding.artifact_id == approval.target_id,
                    FactBinding.placeholder == "fact_expected_emissions",
                )
            )
            binding.display_value = "invented value"
        else:
            approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    detail = await api_client.get(
        f"/api/approvals/{preview['id']}", params={"company_id": str(ids.company_id)}
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["preview_current"] is False
    result = await api_client.post(
        f"/api/approvals/{preview['id']}/decision",
        json={
            "company_id": str(ids.company_id),
            "decision": "approve",
            "preview_hash": preview["preview_hash"],
            "actor_id": str(ids.approver_id),
        },
    )
    assert result.status_code == 409, result.text
    assert result.json()["detail"]["code"] == (
        "approval_expired" if changed == "expiry" else "approval_invalidated"
    )
    async with e2e_context.session_factory() as session:
        approval = await session.get(Approval, UUID(preview["id"]))
        assert approval.status == "pending"
        assert approval.ledger_event_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["fact", "evidence", "claim", "method"])
async def test_disclosure_approval_rechecks_facts_evidence_and_exact_claims(
    api_client, e2e_context, changed
):
    from app.db.models.assurance import DisclosureClaim
    from app.db.models.carbon import CalculationRun, CarbonMeasurement

    ids = e2e_context.ids
    preview = await _disclosure_preview(api_client, e2e_context)
    async with e2e_context.session_factory() as session, session.begin():
        if changed == "fact":
            measurement = await session.get(CarbonMeasurement, ids.measurement_id)
            measurement.value_kgco2e += Decimal(1)
        elif changed == "method":
            measurement = await session.get(CarbonMeasurement, ids.measurement_id)
            calculation = await session.get(CalculationRun, measurement.calculation_run_id)
            method = await session.get(MethodDefinition, calculation.method_definition_id)
            method.configuration = {**method.configuration, "version_changed": True}
        elif changed == "evidence":
            evidence = await session.get(EvidenceItem, ids.assurance_evidence_id)
            evidence.checksum = "c" * 64
        else:
            claim = await session.scalar(
                select(DisclosureClaim).where(
                    DisclosureClaim.disclosure_draft_id == UUID(preview["target_id"]),
                    DisclosureClaim.claim_type == "numeric",
                )
            )
            claim.rendered_text = "Synthetic, but changed after the reviewer saw it."
    result = await api_client.post(
        f"/api/approvals/{preview['id']}/decision",
        json={
            "company_id": str(ids.company_id),
            "decision": "approve",
            "preview_hash": preview["preview_hash"],
            "actor_id": str(ids.approver_id),
        },
    )
    assert result.status_code == 409, result.text
    assert result.json()["detail"]["code"] == "approval_invalidated"


@pytest.mark.asyncio
async def test_approval_failure_rolls_back_target_event_and_decision(
    api_client, e2e_context, monkeypatch
):
    from app.modules.approvals import service

    ids = e2e_context.ids
    preview = await _dispatch_preview(api_client, ids)

    async def fail_lineage(*_args, **_kwargs):
        raise RuntimeError("Synthetic failure while persisting decision lineage")

    monkeypatch.setattr(service, "append_lineage_edge", fail_lineage)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        await api_client.post(
            f"/api/approvals/{preview['id']}/decision",
            json={
                "company_id": str(ids.company_id),
                "decision": "approve",
                "preview_hash": preview["preview_hash"],
                "actor_id": str(ids.approver_id),
            },
        )
    async with e2e_context.session_factory() as session:
        approval = await session.get(Approval, UUID(preview["id"]))
        recommendation = await session.get(DispatchRecommendation, approval.target_id)
        assert approval.status == "pending"
        assert approval.ledger_event_id is None
        assert recommendation.status == "pending_approval"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEvent)
                .where(LedgerEvent.entity_id == approval.id)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_source_updates_wait_until_approval_commit(api_client, e2e_context, monkeypatch):
    from app.modules.approvals import service

    ids = e2e_context.ids
    preview = await _dispatch_preview(api_client, ids)
    reviewing, release, mutating = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = service.generic_preview_is_current

    async def paused_review(*args, **kwargs):
        reviewing.set()
        await asyncio.wait_for(release.wait(), timeout=10)
        return await original(*args, **kwargs)

    async def mutate_method():
        async with e2e_context.session_factory() as session, session.begin():
            mutating.set()
            await session.execute(
                update(MethodDefinition)
                .where(MethodDefinition.id == ids.dispatch_method_id)
                .values(is_active=False)
            )

    monkeypatch.setattr(service, "generic_preview_is_current", paused_review)
    decision = asyncio.create_task(
        api_client.post(
            f"/api/approvals/{preview['id']}/decision",
            json={
                "company_id": str(ids.company_id),
                "decision": "approve",
                "preview_hash": preview["preview_hash"],
                "actor_id": str(ids.approver_id),
            },
        )
    )
    await asyncio.wait_for(reviewing.wait(), timeout=10)
    mutation = asyncio.create_task(mutate_method())
    await asyncio.wait_for(mutating.wait(), timeout=10)
    try:
        # Give the competing UPDATE an opportunity to reach PostgreSQL. It must
        # remain blocked while the decision owns its source row lock.
        await asyncio.sleep(0.1)
        assert not mutation.done()
    finally:
        release.set()
        result, _ = await asyncio.wait_for(asyncio.gather(decision, mutation), timeout=10)
    assert result.status_code == 200, result.text
    detail = await api_client.get(
        f"/api/approvals/{preview['id']}", params={"company_id": str(ids.company_id)}
    )
    assert detail.json()["status"] == "approved"
    assert detail.json()["preview_current"] is False


@pytest.mark.asyncio
async def test_expiration_is_rechecked_after_domain_revalidation(
    api_client, e2e_context, monkeypatch
):
    from app.modules.approvals import service

    ids = e2e_context.ids
    preview = await _dispatch_preview(api_client, ids)
    original = service.generic_preview_is_current

    async def advance_clock_after_review(session, approval, **kwargs):
        current = await original(session, approval, **kwargs)

        class AfterExpiry(datetime):
            @classmethod
            def now(cls, tz=None):
                return approval.expires_at + timedelta(seconds=1)

        monkeypatch.setattr(service, "datetime", AfterExpiry)
        return current

    monkeypatch.setattr(service, "generic_preview_is_current", advance_clock_after_review)
    response = await api_client.post(
        f"/api/approvals/{preview['id']}/decision",
        json={
            "company_id": str(ids.company_id),
            "decision": "approve",
            "preview_hash": preview["preview_hash"],
            "actor_id": str(ids.approver_id),
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "approval_expired"
    async with e2e_context.session_factory() as session:
        approval = await session.get(Approval, UUID(preview["id"]))
        assert approval.status == "pending"
        assert approval.ledger_event_id is None
