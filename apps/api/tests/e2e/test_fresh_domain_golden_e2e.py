"""A complete quarter reaches all three human decisions from actual HTTP imports."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.db.models.carbon import CarbonMeasurement, EmissionCalculation, RawActivityRecord
from app.db.models.ledger import LedgerEvent
from app.modules.demo.fixtures import (
    ASSURANCE_BOUNDARY_REQUIREMENT_ID,
    ASSURANCE_STANDARD_ID,
    ASSURANCE_TOTAL_REQUIREMENT_ID,
    CURRENT_PRODUCT_ID,
    DEMO_ANALYST_ID,
    DEMO_APPROVER_ID,
    DEMO_PROCUREMENT_MANAGER_ID,
    DISPATCH_METHOD_ID,
    DISPATCH_POLICY_ID,
    FLEXIBLE_LOAD_ID,
    SCORING_METHOD_ID,
)
from app.modules.ledger.service import payload_sha256
from tests.e2e.fresh_inputs import prepare_complete_quarter_inputs


@pytest.mark.asyncio
async def test_complete_quarter_fresh_uploads_measurements_lineage_and_three_approvals(
    api_client, e2e_context,
):
    fresh = await prepare_complete_quarter_inputs(api_client, e2e_context)
    company = {"company_id": fresh.context["company_id"]}
    scope2_payload = {
        **fresh.context,
        "output_metric_key": "emissions.scope2.location_based",
        "activity_record_ids": fresh.electricity_activity_ids,
        "actor_id": str(DEMO_ANALYST_ID),
    }
    missing_grid = await api_client.post("/api/measurement/calculate", json=scope2_payload)
    assert missing_grid.status_code == 422
    assert missing_grid.json()["detail"]["code"] == "missing_grid_interval"

    start = datetime.fromisoformat(fresh.manifest["interval_start"])
    end = datetime.fromisoformat(fresh.manifest["interval_end"])
    synchronized = 0
    while start < end:
        chunk_end = min(start + timedelta(hours=240), end)
        synced = await api_client.post(
            "/api/measurement/grid/history/sync",
            params={**company, "site_id": fresh.context["site_id"]},
            json={
                "mode": "fixture",
                "fixture_variant": "complete_q3_v1",
                "start": start.isoformat(),
                "end": chunk_end.isoformat(),
            },
        )
        assert synced.status_code == 200, synced.text
        synchronized += synced.json()["inserted_points"]
        start = chunk_end
    assert synchronized == 2208
    response = await api_client.post("/api/measurement/calculate", json=scope2_payload)
    assert response.status_code == 200, response.text
    scope2 = response.json()
    assert Decimal(scope2["value_kgco2e"]) == Decimal(fresh.manifest["expected"]["scope2_kgco2e"])
    assert scope2["coverage"]["full_reporting_period"] is True
    assert scope2["coverage"]["observed_hours"] == scope2["coverage"]["reporting_period_hours"] == 2208
    assert len(scope2["calculations"]) == len(scope2["grid_points"]) == len(scope2["inputs"]) == 2208
    assert {row["source_document_id"] for row in scope2["inputs"]} == {fresh.electricity_document_id}
    replay = await api_client.post("/api/measurement/calculate", json=scope2_payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == scope2["id"]
    assert replay.json()["output_hash"] == scope2["output_hash"]
    assert replay.json()["idempotent"] is True
    lineage = await api_client.get(f"/api/measurements/{scope2['id']}/lineage", params=company)
    assert lineage.status_code == 200, lineage.text
    assert any(node.get("event_type") == "measurement.grid_point_used" for node in lineage.json()["nodes"])
    assert any(node["node_type"] == "evidence" for node in lineage.json()["nodes"])

    material_response = await api_client.post(
        "/api/measurement/calculate",
        json={
            **fresh.context,
            "material_code": "RECYCLED-ALUMINIUM",
            "activity_record_ids": fresh.material_activity_ids,
            "actor_id": str(DEMO_ANALYST_ID),
        },
    )
    assert material_response.status_code == 200, material_response.text
    material = material_response.json()
    assert Decimal(material["value_kgco2e"]) == Decimal(86000)
    assert {row["source_document_id"] for row in material["inputs"]} == {fresh.material_document_id}

    draft_payload = {
        **fresh.context,
        "standard_id": str(ASSURANCE_STANDARD_ID),
        "measurement_id": scope2["id"],
        "requested_by": str(DEMO_ANALYST_ID),
        "title": "Synthetic complete Q3 location-based Scope 2 disclosure",
    }
    previews = []
    for key, requirements in (
        ("full-template", []),
        ("supported-claims", [str(ASSURANCE_BOUNDARY_REQUIREMENT_ID), str(ASSURANCE_TOTAL_REQUIREMENT_ID)]),
    ):
        created = await api_client.post(
            "/api/assurance/drafts",
            json={**draft_payload, "requirement_ids": requirements, "idempotency_key": f"fresh-{key}"},
        )
        assert created.status_code == 201, created.text
        validated = await api_client.post(
            f"/api/assurance/drafts/{created.json()['id']}/validate",
            json={**company, "requested_by": str(DEMO_ANALYST_ID), "idempotency_key": f"validate-fresh-{key}"},
        )
        assert validated.status_code == 200, validated.text
        result = validated.json()
        assert result["supported_claims"] == 2, result
        if not requirements:
            assert result["terminal_state"] == "unsupported"
            assert result["unsupported_claims"] == 1
            assert result["draft"]["approval"] is None
        else:
            assert result["terminal_state"] == "approval_required", result
            previews.append(result["draft"]["approval"])
            evidence_pack = await api_client.get(
                f"/api/assurance/drafts/{created.json()['id']}/evidence-pack", params=company
            )
            assert evidence_pack.status_code == 200, evidence_pack.text
            assert evidence_pack.json()["fact_bindings"][0]["ledger_event_id"] == scope2["facts"]["ledger_event_id"]

    procurement = await api_client.post(
        "/api/procurement/scenarios",
        json={
            **fresh.context,
            "current_product_id": str(CURRENT_PRODUCT_ID),
            "carbon_measurement_id": material["id"],
            "method_definition_id": str(SCORING_METHOD_ID),
            "requested_by": str(DEMO_PROCUREMENT_MANAGER_ID),
            "quantity": "10000", "quantity_unit": "kg",
            "max_cost_increase_pct": "5", "max_lead_time_days": 30,
            "minimum_circularity_score": "0",
        },
    )
    assert procurement.status_code == 201, procurement.text
    previews.append(procurement.json()["selected_recommendation"]["approval"])
    forecast = await api_client.post(
        "/api/dispatch/forecasts/sync",
        json={**company, "site_id": fresh.context["site_id"], "source_mode": "fixture"},
    )
    assert forecast.status_code == 200, forecast.text
    dispatch = await api_client.post(
        "/api/dispatch/scenarios",
        json={
            **company, "site_id": fresh.context["site_id"],
            "flexible_load_id": str(FLEXIBLE_LOAD_ID),
            "method_definition_id": str(DISPATCH_METHOD_ID),
            "policy_definition_id": str(DISPATCH_POLICY_ID),
            "forecast_source_document_id": forecast.json()["source_document_id"],
            "requested_by": str(DEMO_ANALYST_ID),
            "window_start": "2026-10-01T08:00:00Z", "window_end": "2026-10-01T20:00:00Z",
            "baseline_start": "2026-10-01T08:00:00Z", "maximum_delay_minutes": 240,
        },
    )
    assert dispatch.status_code == 201, dispatch.text
    optimized = await api_client.post(
        f"/api/dispatch/scenarios/{dispatch.json()['id']}/optimize", json=company
    )
    assert optimized.status_code == 200, optimized.text
    assert optimized.json()["terminal_state"] == "approval_required"
    previews.append(optimized.json()["recommendation"]["approval"])
    target_types = set()
    for preview in previews:
        detail = await api_client.get(f"/api/approvals/{preview['id']}", params=company)
        assert detail.status_code == 200, detail.text
        target_types.add(detail.json()["target_type"])
        assert detail.json()["preview_current"] is True
        assert payload_sha256(detail.json()["preview_payload"])[1] == preview["preview_hash"]
        decision_payload = {
            **company, "actor_id": str(DEMO_APPROVER_ID), "decision": "approve",
            "preview_hash": preview["preview_hash"],
            "decision_note": "Synthetic complete-quarter reviewer approved exact facts and payload.",
        }
        decision = await api_client.post(f"/api/approvals/{preview['id']}/decision", json=decision_payload)
        assert decision.status_code == 200, decision.text
        assert decision.json()["status"] == "approved"
        replayed = await api_client.post(f"/api/approvals/{preview['id']}/decision", json=decision_payload)
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["idempotent_replay"] is True
        assert replayed.json()["ledger_event_id"] == decision.json()["ledger_event_id"]
    assert target_types == {
        "disclosure_draft", "procurement_recommendation", "dispatch_recommendation"
    }
    async with e2e_context.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CarbonMeasurement)) == 2
        assert await session.scalar(select(func.count()).select_from(EmissionCalculation)) == 2209
        assert await session.scalar(select(func.count()).select_from(RawActivityRecord).where(
            RawActivityRecord.source_document_id == UUID(fresh.electricity_document_id)
        )) == 2208
        assert await session.scalar(select(func.count()).select_from(LedgerEvent).where(
            LedgerEvent.event_type == "approval.approved"
        )) == 3
