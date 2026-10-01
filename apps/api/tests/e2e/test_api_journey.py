from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.main import app
from app.modules.integrations.electricity_maps import get_electricity_maps_client
from tests.e2e.conftest import E2EContext


async def _wait_for_agent_terminal(
    client: httpx.AsyncClient,
    *,
    run_id: str,
    company_id: str,
) -> httpx.Response:
    for _ in range(100):
        response = await client.get(
            f"/api/runs/{run_id}",
            params={"company_id": company_id},
        )
        assert response.status_code == 200
        if response.json()["terminal_state"] != "running":
            return response
        await asyncio.sleep(0.05)
    pytest.fail("agent run did not reach a terminal state within the bounded poll window")


class FakeElectricityMapsProvider:
    async def list_zones(self) -> dict[str, Any]:
        return {
            "IN": {
                "zoneKey": "IN",
                "displayName": "India",
                "countryCode": "IN",
                "access": ["carbon-intensity/past-range", "carbon-intensity/latest"],
            }
        }

    async def get_carbon_intensity_range(
        self,
        *,
        zone: str,
        start,
        end,
        disable_estimations: bool = False,
    ) -> dict[str, Any]:
        del end, disable_estimations
        return {
            "zone": zone,
            "temporalGranularity": "hourly",
            "data": [
                {
                    "carbonIntensity": 600,
                    "datetime": start.isoformat(),
                    "updatedAt": (start + timedelta(minutes=5)).isoformat(),
                    "emissionFactorType": "lifecycle",
                    "flowTraced": True,
                    "isEstimated": False,
                    "estimationMethod": None,
                    "temporalGranularity": "hourly",
                },
                {
                    "carbonIntensity": 550,
                    "datetime": (start + timedelta(hours=1)).isoformat(),
                    "updatedAt": (start + timedelta(hours=1, minutes=5)).isoformat(),
                    "emissionFactorType": "lifecycle",
                    "flowTraced": True,
                    "isEstimated": True,
                    "estimationMethod": "FORECASTS_HIERARCHY",
                    "temporalGranularity": "hourly",
                },
            ],
        }


@pytest.mark.asyncio
async def test_all_requested_apis_as_one_synthetic_journey(
    api_client: httpx.AsyncClient,
    e2e_context: E2EContext,
    expected_demo_results: dict[str, object],
) -> None:
    ids = e2e_context.ids
    expected = expected_demo_results
    assert expected["synthetic"] is True
    company_params = {"company_id": str(ids.company_id)}
    app.dependency_overrides[get_electricity_maps_client] = FakeElectricityMapsProvider
    try:
        lineage_response = await api_client.get(
            f"/api/measurements/{ids.measurement_id}/lineage",
            params=company_params,
        )
        assert lineage_response.status_code == 200
        lineage = lineage_response.json()
        assert lineage["root_event_id"] == str(ids.measurement_ledger_event_id)
        assert len(lineage["nodes"]) == 5
        assert {edge["relationship_type"] for edge in lineage["edges"]} == {
            "activity_input",
            "factor_input",
            "produces",
            "supported_by",
        }

        suppliers_response = await api_client.get(
            "/api/suppliers",
            params={**company_params, "material_code": "PACKAGING-TRAY"},
        )
        assert suppliers_response.status_code == 200
        suppliers = suppliers_response.json()
        assert suppliers["total"] == 3
        assert all(item["evidence_available"] for item in suppliers["items"])
        assert {item["risk"] for item in suppliers["items"]} == {"low", "medium"}

        supplier_response = await api_client.get(
            f"/api/suppliers/{ids.recommended_product_id}",
            params=company_params,
        )
        assert supplier_response.status_code == 200
        supplier = supplier_response.json()
        assert Decimal(supplier["pcf_kgco2e_per_unit"]) == Decimal(
            str(expected["recommended_product_pcf_kgco2e_per_kg"])
        )
        assert supplier["evidence"]["metadata"]["synthetic"] is True

        scenario_response = await api_client.post(
            "/api/procurement/scenarios",
            json={
                "company_id": str(ids.company_id),
                "site_id": str(ids.site_id),
                "reporting_period_id": str(ids.reporting_period_id),
                "current_product_id": str(ids.current_product_id),
                "carbon_measurement_id": str(ids.measurement_id),
                "method_definition_id": str(ids.scoring_method_id),
                "requested_by": str(ids.procurement_manager_id),
                "quantity": "12000",
                "quantity_unit": "kg",
                "max_cost_increase_pct": "5",
                "max_lead_time_days": 20,
                "minimum_circularity_score": "50",
                "material_constraints": {"allowed_material_codes": ["PACKAGING-TRAY"]},
            },
        )
        assert scenario_response.status_code == 201
        scenario = scenario_response.json()
        assert scenario["terminal_state"] == "completed"
        assert scenario["status"] == "recommended"
        assert len(scenario["alternatives"]) == 2
        selected = scenario["selected_recommendation"]
        assert selected["recommended_product_id"] == str(ids.recommended_product_id)
        assert Decimal(selected["impact"]["projected_footprint_kgco2e"]) == Decimal(
            str(expected["projected_footprint_kgco2e"])
        )
        assert Decimal(selected["impact"]["avoided_kgco2e"]) == Decimal(
            str(expected["avoided_emissions_kgco2e"])
        )
        assert Decimal(selected["impact"]["cost_delta_pct"]) == Decimal(
            str(expected["recommended_cost_delta_pct"])
        )
        scenario_id = scenario["id"]
        recommendation_id = selected["id"]
        approval_id = selected["approval"]["id"]
        preview_hash = selected["approval"]["preview_hash"]

        assessment_response = await api_client.post(
            f"/api/procurement/scenarios/{scenario_id}/score",
            json={"company_id": str(ids.company_id)},
        )
        assert assessment_response.status_code == 200
        assessment = assessment_response.json()
        assert assessment["terminal_state"] == "completed"
        assert assessment["selected_product_id"] == str(ids.recommended_product_id)
        expensive = next(
            item
            for item in assessment["assessments"]
            if item["product"]["id"] == str(ids.expensive_product_id)
        )
        assert expensive["feasible"] is False
        assert {reason["code"] for reason in expensive["infeasibility_reasons"]} == {
            "cost_ceiling_exceeded"
        }

        scenario_read_response = await api_client.get(
            f"/api/procurement/scenarios/{scenario_id}",
            params=company_params,
        )
        assert scenario_read_response.status_code == 200
        assert scenario_read_response.json()["analysis_signature"] == scenario["analysis_signature"]

        recommendation_response = await api_client.get(
            f"/api/procurement/recommendations/{recommendation_id}",
            params=company_params,
        )
        assert recommendation_response.status_code == 200
        recommendation = recommendation_response.json()
        assert len(recommendation["payload_hash"]) == 64
        assert recommendation["narrative"]["unsupported_fragments"] == []
        assert "10800" in recommendation["narrative"]["resolved_text"]

        agent_response = await api_client.post(
            "/api/agent/requests",
            json={
                "query": (
                    "Measure the Plant B packaging footprint and recommend a lower-carbon "
                    "supplier under the cost constraint."
                ),
                "context": {
                    "company_id": str(ids.company_id),
                    "actor_id": str(ids.procurement_manager_id),
                    "site_id": str(ids.site_id),
                    "reporting_period_id": str(ids.reporting_period_id),
                    "metric_keys": [],
                    "material_scope": ["PACKAGING-TRAY"],
                    "supplier_product_ids": [],
                    "constraints": {
                        "max_cost_increase_pct": "5",
                        "max_lead_time_days": 20,
                        "minimum_circularity_score": "50",
                    },
                },
            },
        )
        assert agent_response.status_code == 202
        accepted_run = agent_response.json()
        assert accepted_run["terminal_state"] == "running"
        run_id = accepted_run["run_id"]

        run_response = await _wait_for_agent_terminal(
            api_client,
            run_id=run_id,
            company_id=str(ids.company_id),
        )
        assert run_response.status_code == 200
        run = run_response.json()
        assert run["terminal_state"] == "completed"
        assert run["workflow"] == "cross_module"
        assert run["telemetry"]["model_calls"] == 0
        assert run["telemetry"]["tool_calls"] == 3
        assert len(run["facts"]) == 3
        assert run["recommendation"]["recommendation_id"] == recommendation_id
        assert run["approval_requirement"]["approval_id"] == approval_id

        events_response = await api_client.get(
            f"/api/runs/{run_id}/events", params=company_params
        )
        assert events_response.status_code == 200
        assert "event: run.started\n" in events_response.text
        assert "event: run.completed\n" in events_response.text

        approvals_response = await api_client.get(
            "/api/approvals", params={**company_params, "status": "pending"}
        )
        assert approvals_response.status_code == 200
        approvals = approvals_response.json()
        assert approvals["total"] == 1
        assert approvals["items"][0]["preview_current"] is True

        stale_decision = await api_client.post(
            f"/api/approvals/{approval_id}/decision",
            json={
                "company_id": str(ids.company_id),
                "decision": "approve",
                "preview_hash": "0" * 64,
                "actor_id": str(ids.approver_id),
                "decision_note": "Synthetic stale-preview check.",
            },
        )
        assert stale_decision.status_code == 409
        assert stale_decision.json()["detail"]["code"] == "approval_invalidated"

        decision_response = await api_client.post(
            f"/api/approvals/{approval_id}/decision",
            json={
                "company_id": str(ids.company_id),
                "decision": "approve",
                "preview_hash": preview_hash,
                "actor_id": str(ids.approver_id),
                "decision_note": "Approved in the synthetic API journey.",
            },
        )
        assert decision_response.status_code == 200
        decision = decision_response.json()
        assert decision["status"] == "approved"
        assert decision["idempotent_replay"] is False

        replay_response = await api_client.post(
            f"/api/approvals/{approval_id}/decision",
            json={
                "company_id": str(ids.company_id),
                "decision": "approve",
                "preview_hash": preview_hash,
                "actor_id": str(ids.approver_id),
                "decision_note": "The note is ignored on an idempotent replay.",
            },
        )
        assert replay_response.status_code == 200
        assert replay_response.json()["idempotent_replay"] is True
        assert replay_response.json()["ledger_event_id"] == decision["ledger_event_id"]

        audit_response = await api_client.get(
            f"/api/audit/recommendation/{recommendation_id}",
            params=company_params,
        )
        assert audit_response.status_code == 200
        audit = audit_response.json()
        assert any(item["action"] == "approval.approved" for item in audit["timeline"])
        assert any(edge["relationship_type"] == "decided_by" for edge in audit["lineage"]["edges"])

        integration_test_response = await api_client.post(
            "/api/integrations/electricity-maps/test?max_zones=10"
        )
        assert integration_test_response.status_code == 200
        integration_test = integration_test_response.json()
        assert integration_test["authenticated"] is True
        assert integration_test["accessible_zone_count"] == 1
        assert "token" not in integration_test_response.text.casefold()

        sync_response = await api_client.post(
            "/api/measurement/grid/history/sync",
            params={**company_params, "site_id": str(ids.site_id)},
            json={
                "zone": "IN",
                "start": "2026-09-29T00:00:00Z",
                "end": "2026-09-29T02:00:00Z",
                "disable_estimations": False,
            },
        )
        assert sync_response.status_code == 200
        sync = sync_response.json()
        assert sync["received_points"] == 2
        assert sync["inserted_factors"] == 2
        assert sync["estimated_points"] == 1
        assert len(sync["response_checksum"]) == 64

        latest_response = await api_client.get(
            "/api/measurement/grid/latest",
            params={**company_params, "site_id": str(ids.site_id)},
        )
        assert latest_response.status_code == 200
        latest = latest_response.json()
        assert Decimal(latest["value"]) == Decimal("0.55")
        assert Decimal(latest["provider_value_gco2eq_per_kwh"]) == Decimal(550)
        assert latest["is_estimated"] is True
        assert latest["provenance"]["provider"] == "electricity_maps"
    finally:
        app.dependency_overrides.pop(get_electricity_maps_client, None)
