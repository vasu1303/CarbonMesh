"""Production tool composition builds artifacts from source uploads, then resumes approvals."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

import app.modules.agents.runtime as agent_runtime
from app.api.routes.agents import get_agent_run_service_factory
from app.db.models.carbon import CarbonMeasurement, EmissionCalculation
from app.db.models.core import Approval
from app.db.models.dispatch import DispatchRecommendation
from app.db.models.procurement import Recommendation
from app.main import app
from app.modules.demo.fixtures import (
    ASSURANCE_BOUNDARY_REQUIREMENT_ID,
    ASSURANCE_STANDARD_ID,
    ASSURANCE_TOTAL_REQUIREMENT_ID,
    CURRENT_PRODUCT_ID,
    DEMO_ANALYST_ID,
    DEMO_APPROVER_ID,
    DISPATCH_METHOD_ID,
    DISPATCH_POLICY_ID,
    FLEXIBLE_LOAD_ID,
    SCORING_METHOD_ID,
)
from tests.e2e.fresh_inputs import prepare_complete_quarter_inputs
from tests.e2e.test_agent_four_module_e2e import (
    FourModuleE2EPlanner,
    _approve_and_resume,
    _wait_for_terminal_run,
)


@pytest.mark.asyncio
async def test_fresh_upload_agent_creates_all_artifacts_and_three_exact_approvals(
    api_client, e2e_context, monkeypatch,
):
    fresh = await prepare_complete_quarter_inputs(api_client, e2e_context)
    monkeypatch.setattr(agent_runtime, "build_ai_model", FourModuleE2EPlanner)
    previous = app.dependency_overrides[get_agent_run_service_factory]
    app.dependency_overrides[get_agent_run_service_factory] = lambda: get_agent_run_service_factory()
    try:
        response = await api_client.post("/api/agent/requests", json={
            "query": "Calculate Measurement, draft Assurance, compare Procurement and optimize advisory Dispatch.",
            "context": {
                **fresh.context, "actor_id": str(DEMO_ANALYST_ID), "grid_source_mode": "fixture",
                "current_product_id": str(CURRENT_PRODUCT_ID),
                "standard_id": str(ASSURANCE_STANDARD_ID),
                "requirement_ids": [str(ASSURANCE_BOUNDARY_REQUIREMENT_ID), str(ASSURANCE_TOTAL_REQUIREMENT_ID)],
                "flexible_load_id": str(FLEXIBLE_LOAD_ID), "policy_definition_id": str(DISPATCH_POLICY_ID),
                "material_scope": ["RECYCLED-ALUMINIUM"],
                "constraints": {"max_cost_increase_pct": "5", "max_lead_time_days": 30, "minimum_circularity_score": "0"},
                "dispatch_constraints": {
                    "window_start": "2026-10-01T08:00:00Z", "window_end": "2026-10-01T20:00:00Z",
                    "duration_minutes": 120, "max_delay_minutes": 240, "maximum_power_kw": "500",
                },
                "fresh_inputs": {
                    "history_start": fresh.manifest["interval_start"], "history_end": fresh.manifest["interval_end"],
                    "history_fixture_variant": "complete_q3_v1",
                    "measurements": [
                        {"material_code": "ELECTRICITY", "output_metric_key": "emissions.scope2.location_based",
                         "activity_record_ids": fresh.electricity_activity_ids},
                        {"material_code": "RECYCLED-ALUMINIUM", "output_metric_key": "emissions.scope3.category1",
                         "activity_record_ids": fresh.material_activity_ids},
                    ],
                    "procurement_quantity": "10000", "procurement_method_id": str(SCORING_METHOD_ID),
                    "dispatch_method_id": str(DISPATCH_METHOD_ID), "dispatch_baseline_start": "2026-10-01T08:00:00Z",
                },
            },
        })
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        company_id = fresh.context["company_id"]
        counts = []
        for index, target in enumerate(("disclosure_draft", "procurement_recommendation", "dispatch_recommendation")):
            run = await _wait_for_terminal_run(api_client, run_id=run_id, company_id=company_id)
            if run["terminal_state"] != "approval_required":
                events = await api_client.get(f"/api/runs/{run_id}/events", params={"company_id": company_id})
                print(events.text[-5000:])
            assert run["terminal_state"] == "approval_required", {
                "state": run["terminal_state"], "code": run["error_code"],
                "missing": run["missing_fields"], "graph": run["telemetry"].get("graph_state"),
            }
            assert run["pending_interrupt"]["target_type"] == target
            counts.append(run["telemetry"]["tool_calls"])
            await _approve_and_resume(api_client, run=run, company_id=company_id,
                                      approver_id=str(DEMO_APPROVER_ID), idempotency_key=f"fresh-agent-approval-{index}")
        run = await _wait_for_terminal_run(api_client, run_id=run_id, company_id=company_id)
        assert run["terminal_state"] == "success", run["error_code"]
        assert counts == [6, 9, 12]
        assert run["telemetry"]["tool_calls"] == 12
        assert run["telemetry"]["model_calls"] == 1
        assert run["telemetry"]["elapsed_ms"] <= 45000
        facts = {item["metric_key"]: item for item in run["facts"]}
        assert {"emissions.scope2.location_based", "emissions.scope3.category1",
                "procurement.projected_avoided_emissions", "dispatch.avoided_emissions"} <= facts.keys()
        assert "fact:" in run["message"]
        assert run["telemetry"]["sustainability_assumptions"]["version"] == "1.0"
        async with e2e_context.session_factory() as session:
            measurements = list(await session.scalars(select(CarbonMeasurement)))
            assert len(measurements) == 2
            assert {item.value_kgco2e for item in measurements} == {
                Decimal(fresh.manifest["expected"]["scope2_kgco2e"]), Decimal(86000)}
            assert await session.scalar(select(func.count()).select_from(EmissionCalculation)) == 2209
            approvals = list(await session.scalars(select(Approval)))
            assert len(approvals) == 3
            assert all(item.status == "approved" for item in approvals)
        sustainability = await api_client.get("/api/metrics/agent-sustainability", params={"company_id": company_id})
        assert sustainability.status_code == 200, sustainability.text
        metrics = sustainability.json()
        assert {item["target_type"] for item in metrics["benefit_facts"]} == {
            "procurement_recommendation", "dispatch_recommendation"}
        assert all(item["ledger_event_id"] for item in metrics["benefit_facts"])
        assert Decimal(metrics["business_benefit_kgco2e"]) > 0
        assert Decimal(metrics["benefit_to_footprint_ratio"]) > 0
        events = await api_client.get(f"/api/runs/{run_id}/events", params={"company_id": company_id})
        assert events.text.count("event: approval.required\n") == 3
        assert events.text.count("event: run.completed\n") == 1
        for model in (Recommendation, DispatchRecommendation):
            async with e2e_context.session_factory() as session:
                recommendation = await session.scalar(select(model))
                original = recommendation.avoided_kgco2e
                recommendation.avoided_kgco2e += Decimal(1)
                await session.commit()
                invalid = await api_client.get(
                    "/api/metrics/agent-sustainability", params={"company_id": company_id},
                )
                assert invalid.status_code == 409, invalid.text
                assert invalid.json()["detail"]["code"] == "agent_metrics_fact_invalid"
                recommendation.avoided_kgco2e = original
                await session.commit()
    finally:
        app.dependency_overrides[get_agent_run_service_factory] = previous
