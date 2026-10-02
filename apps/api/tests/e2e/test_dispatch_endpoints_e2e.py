from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.db.models.core import Approval, DataSource, SourceDocument
from app.db.models.dispatch import DispatchRecommendation, DispatchScenario, GridForecast
from app.db.models.ledger import FactBinding, LedgerEvent, LedgerEventEvidence, LineageEdge
from tests.e2e.conftest import E2EContext


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_dispatch_api_vertical_slice_is_deterministic_and_advisory(
    api_client,
    e2e_context: E2EContext,
    decision: str,
) -> None:
    ids = e2e_context.ids
    company_query = {"company_id": str(ids.company_id)}

    loads_response = await api_client.get("/api/dispatch/loads", params=company_query)
    assert loads_response.status_code == 200, loads_response.text
    loads = loads_response.json()
    assert loads["total"] == 1
    assert loads["items"][0]["code"] == "BATCH-PROCESS-7"
    assert len(loads["items"][0]["constraints"]) == 4
    assert loads["items"][0]["metadata"]["actuation_authorized"] is False

    sync_response = await api_client.post(
        "/api/dispatch/forecasts/sync",
        json={
            "company_id": str(ids.company_id),
            "site_id": str(ids.site_id),
            "source_mode": "fixture",
        },
    )
    assert sync_response.status_code == 200, sync_response.text
    synced = sync_response.json()
    assert synced["received_points"] == 24
    assert synced["inserted_points"] == 24
    assert synced["existing_points"] == 0
    assert synced["estimated_points"] == 24
    assert all(len(item["point_hash"]) == 64 for item in synced["points"])

    repeated_sync = await api_client.post(
        "/api/dispatch/forecasts/sync",
        json={
            "company_id": str(ids.company_id),
            "site_id": str(ids.site_id),
            "source_mode": "fixture",
        },
    )
    assert repeated_sync.status_code == 200, repeated_sync.text
    assert repeated_sync.json()["source_document_id"] == synced["source_document_id"]
    assert repeated_sync.json()["inserted_points"] == 0
    assert repeated_sync.json()["existing_points"] == 24

    scenario_payload = {
        "company_id": str(ids.company_id),
        "site_id": str(ids.site_id),
        "flexible_load_id": str(ids.flexible_load_id),
        "method_definition_id": str(ids.dispatch_method_id),
        "policy_definition_id": str(ids.dispatch_policy_id),
        "forecast_source_document_id": synced["source_document_id"],
        "requested_by": str(ids.analyst_id),
        "window_start": "2026-10-01T08:00:00Z",
        "window_end": "2026-10-01T20:00:00Z",
        "baseline_start": "2026-10-01T08:00:00Z",
        "maximum_delay_minutes": 240,
    }
    scenario_response = await api_client.post(
        "/api/dispatch/scenarios",
        json=scenario_payload,
    )
    assert scenario_response.status_code == 201, scenario_response.text
    scenario = scenario_response.json()
    assert scenario["status"] == "draft"
    assert scenario["objective"] == "minimum_carbon"
    assert len(scenario["analysis_signature"]) == 64
    assert len(scenario["forecast_snapshot"]) == 24

    optimize_response = await api_client.post(
        f"/api/dispatch/scenarios/{scenario['id']}/optimize",
        json={"company_id": str(ids.company_id)},
    )
    assert optimize_response.status_code == 200, optimize_response.text
    optimized = optimize_response.json()
    assert optimized["terminal_state"] == "approval_required"
    assert optimized["feasible_windows"] == 3
    recommendation = optimized["recommendation"]
    assert recommendation["baseline_start"] == "2026-10-01T08:00:00Z"
    assert recommendation["baseline_end"] == "2026-10-01T10:00:00Z"
    assert recommendation["recommended_start"] == "2026-10-01T11:00:00Z"
    assert recommendation["recommended_end"] == "2026-10-01T13:00:00Z"
    assert recommendation["baseline_emissions_kgco2e"] == "470.000000"
    assert recommendation["expected_emissions_kgco2e"] == "290.000000"
    assert recommendation["avoided_kgco2e"] == "180.000000"
    assert recommendation["reduction_pct"] == "38.2979"
    assert recommendation["actuation_authorized"] is False
    assert len(recommendation["evidence_item_ids"]) == 24
    assert recommendation["approval"]["target_id"] == recommendation["id"]
    assert recommendation["approval"]["preview_payload"]["target_id"] == recommendation["id"]
    assert recommendation["approval"]["preview_hash"] == recommendation["payload_hash"]
    preview = recommendation["approval"]["preview_payload"]
    assert preview["actuation_authorized"] is False
    assert preview["forecast_source_document_id"] == synced["source_document_id"]
    assert preview["forecast_source_document_checksum"] == synced["snapshot_hash"]
    assert len(preview["forecast_points"]) == 24
    assert all(item["evidence_item_id"] for item in preview["forecast_points"])
    assert all(item["evidence_checksum"] for item in preview["forecast_points"])
    assert preview["load_snapshot"]["code"] == "BATCH-PROCESS-7"
    assert preview["frozen_constraints"]["source_constraints"]

    read_response = await api_client.get(
        f"/api/dispatch/scenarios/{scenario['id']}/recommendation",
        params=company_query,
    )
    assert read_response.status_code == 200, read_response.text
    assert read_response.json()["recommendation"]["id"] == recommendation["id"]

    no_feasible_response = await api_client.post(
        "/api/dispatch/scenarios",
        json={
            **scenario_payload,
            "available_capacity_kw": "499",
        },
    )
    assert no_feasible_response.status_code == 201, no_feasible_response.text
    no_feasible_scenario = no_feasible_response.json()
    infeasible_optimization = await api_client.post(
        f"/api/dispatch/scenarios/{no_feasible_scenario['id']}/optimize",
        json={"company_id": str(ids.company_id)},
    )
    assert infeasible_optimization.status_code == 200, infeasible_optimization.text
    assert infeasible_optimization.json()["terminal_state"] == "no_feasible_option"
    assert infeasible_optimization.json()["recommendation"] is None
    replayed_infeasible = await api_client.post(
        f"/api/dispatch/scenarios/{no_feasible_scenario['id']}/optimize",
        json={"company_id": str(ids.company_id)},
    )
    assert replayed_infeasible.status_code == 200, replayed_infeasible.text
    assert replayed_infeasible.json() == infeasible_optimization.json()

    async with e2e_context.session_factory() as session:
        bindings = list((await session.scalars(select(FactBinding))).all())
        assert len(bindings) == 4
        assert all(binding.agent_run_id is None for binding in bindings)
        assert all(binding.binding_hash and binding.context_hash for binding in bindings)
        assert await session.scalar(select(func.count()).select_from(GridForecast)) == 24
        assert await session.scalar(select(func.count()).select_from(SourceDocument)) == 2
        assert await session.scalar(select(func.count()).select_from(DataSource)) == 2
        assert await session.scalar(select(func.count()).select_from(DispatchScenario)) == 2
        assert await session.scalar(select(func.count()).select_from(DispatchRecommendation)) == 1
        assert await session.scalar(
            select(func.count()).select_from(Approval).where(
                Approval.target_type == "dispatch_recommendation"
            )
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(LineageEdge).where(
                LineageEdge.relationship_type
                == "forecast_input_to_dispatch_recommendation"
            )
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(LedgerEventEvidence).where(
                LedgerEventEvidence.ledger_event_id
                == UUID(recommendation["ledger_event_id"])
            )
        ) == 24
        no_feasible_event = await session.scalar(
            select(LedgerEvent).where(
                LedgerEvent.company_id == ids.company_id,
                LedgerEvent.event_type == "dispatch_no_feasible_window",
                LedgerEvent.entity_id == UUID(no_feasible_scenario["id"]),
            )
        )
        assert no_feasible_event is not None
        assert no_feasible_event.payload["baseline"] == {
            "start": "2026-10-01T08:00:00+00:00",
            "end": "2026-10-01T10:00:00+00:00",
            "emissions_kgco2e": "470.000000",
        }

    decided = await api_client.post(
        f"/api/approvals/{recommendation['approval']['id']}/decision",
        json={
            **company_query,
            "actor_id": str(ids.approver_id),
            "decision": decision,
            "preview_hash": recommendation["approval"]["preview_hash"],
            "decision_note": "Synthetic human-reviewed advisory decision.",
        },
    )
    assert decided.status_code == 200, decided.text
    terminal = "approved" if decision == "approve" else "rejected"
    for result in (
        await api_client.get(
            f"/api/dispatch/scenarios/{scenario['id']}/recommendation", params=company_query
        ),
        await api_client.post(
            f"/api/dispatch/scenarios/{scenario['id']}/optimize", json=company_query
        ),
    ):
        assert result.status_code == 200, result.text
        assert result.json()["terminal_state"] == terminal
        assert result.json()["recommendation"]["id"] == recommendation["id"]
        assert result.json()["recommendation"]["approval"]["status"] == terminal
        assert result.json()["recommendation"]["actuation_authorized"] is False
