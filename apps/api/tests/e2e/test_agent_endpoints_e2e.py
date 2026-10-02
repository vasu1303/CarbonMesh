from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx
import pytest

from app.api.routes.agents import get_agent_run_service_factory
from app.main import app
from app.modules.agents.llm.factory import build_ai_model

LIVE_LLM_E2E_ENV = "CARBONMESH_RUN_LIVE_LLM_E2E"
LIVE_LLM_E2E_OPT_IN = "paid-provider-call"


async def _wait_for_terminal_run(
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


@pytest.mark.asyncio
async def test_agent_clarification_and_approval_resumes_are_durable_and_idempotent(
    api_client,
    e2e_context,
) -> None:
    ids = e2e_context.ids
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
            "quantity": "10000",
            "quantity_unit": "kg",
            "max_cost_increase_pct": "5",
            "max_lead_time_days": 30,
            "minimum_circularity_score": "0",
            "material_constraints": {
                "allowed_material_codes": ["RECYCLED-ALUMINIUM"]
            },
        },
    )
    assert scenario_response.status_code == 201
    scenario_id = scenario_response.json()["id"]

    response = await api_client.post(
        "/api/agent/requests",
        json={
            "query": (
                "Measure the Plant B recycled aluminium footprint and recommend a lower-carbon "
                "supplier without increasing unit cost by more than five percent."
            ),
            "context": {
                "company_id": str(ids.company_id),
                "actor_id": str(ids.procurement_manager_id),
                "site_id": str(ids.site_id),
                "reporting_period_id": str(ids.reporting_period_id),
                "metric_keys": [],
                "material_scope": ["RECYCLED-ALUMINIUM"],
                "supplier_product_ids": [],
                "constraints": {
                    "max_cost_increase_pct": "5",
                    "max_lead_time_days": 30,
                    "minimum_circularity_score": "0",
                },
            },
        },
    )

    assert response.status_code == 202
    accepted = response.json()
    assert accepted["terminal_state"] == "running"

    run_response = await _wait_for_terminal_run(
        api_client,
        run_id=accepted["run_id"],
        company_id=str(ids.company_id),
    )

    assert run_response.status_code == 200
    run = run_response.json()
    assert run["workflow"] == "cross_module"
    assert run["stage"] == "interrupted"
    assert run["terminal_state"] == "needs_clarification"
    assert run["error_code"] == "context_incomplete"
    assert run["missing_fields"] == ["context.procurement_scenario_id"]
    assert run["pending_interrupt"]["kind"] == "clarification"
    assert run["pending_interrupt"]["status"] == "pending"
    assert run["pending_interrupt"]["missing_fields"] == [
        "context.procurement_scenario_id"
    ]
    assert run["facts"] == []
    assert run["recommendation"] is None
    assert run["approval_requirement"]["required"] is False
    assert run["telemetry"]["provider"] == "gemini"
    assert run["telemetry"]["provider_status"] == "completed"
    assert run["telemetry"]["model_id"] == "e2e-strict-structured-planner-v1"
    assert run["telemetry"]["model_calls"] == 1
    assert run["telemetry"]["tool_calls"] == 0
    assert run["telemetry"]["planning_selection"] == {
        "disposition": "execute",
        "modules": ["measurement", "procurement"],
        "clarification_fields": [],
        "reason_code": "synthetic_cross_module_request",
    }
    assert len(run["context"]["analysis_signature"]) == 64
    assert run["telemetry"]["analysis_signature"] == run["context"]["analysis_signature"]

    cross_tenant_response = await api_client.get(
        f"/api/runs/{accepted['run_id']}",
        params={"company_id": str(uuid4())},
    )
    assert cross_tenant_response.status_code == 404

    stream_response = await api_client.get(
        f"/api/runs/{accepted['run_id']}/events",
        params={"company_id": str(ids.company_id)},
    )

    assert stream_response.status_code == 200
    assert stream_response.headers["content-type"].startswith("text/event-stream")
    assert "event: run.started\n" in stream_response.text
    assert "event: provider.completed\n" in stream_response.text
    assert "event: clarification.required\n" in stream_response.text
    assert "event: run.stopped\n" in stream_response.text
    assert "event: tool.completed\n" not in stream_response.text
    assert "event: fact.created\n" not in stream_response.text
    assert "event: approval.required\n" not in stream_response.text
    assert "event: run.completed\n" not in stream_response.text
    assert stream_response.text.endswith("\n\n")

    event_ids = [
        int(line.removeprefix("id: "))
        for line in stream_response.text.splitlines()
        if line.startswith("id: ")
    ]
    final_sequence = max(event_ids)
    cursor_replay = await api_client.get(
        f"/api/runs/{accepted['run_id']}/events",
        params={"company_id": str(ids.company_id)},
        headers={"Last-Event-ID": str(final_sequence - 1)},
    )

    assert cursor_replay.status_code == 200
    assert cursor_replay.text.count("event: run.stopped\n") == 1
    assert "event: clarification.required\n" not in cursor_replay.text
    assert "event: run.started\n" not in cursor_replay.text

    pending = run["pending_interrupt"]
    resume_payload = {
        "company_id": str(ids.company_id),
        "actor_id": str(ids.procurement_manager_id),
        "interrupt_id": pending["interrupt_id"],
        "interrupt_sequence": pending["sequence"],
        "analysis_signature": pending["analysis_signature"],
        "idempotency_key": "synthetic-clarification-resume-001",
        "clarification": {
            "context": {
                "company_id": str(ids.company_id),
                "actor_id": str(ids.procurement_manager_id),
                "site_id": str(ids.site_id),
                "reporting_period_id": str(ids.reporting_period_id),
                "current_product_id": str(ids.current_product_id),
                "procurement_scenario_id": scenario_id,
                "metric_keys": [],
                "material_scope": ["RECYCLED-ALUMINIUM"],
                "supplier_product_ids": [],
                "constraints": {
                    "max_cost_increase_pct": "5",
                    "max_lead_time_days": 30,
                    "minimum_circularity_score": "0",
                },
            }
        },
    }
    resume_response = await api_client.post(
        f"/api/runs/{accepted['run_id']}/resume",
        json=resume_payload,
    )

    assert resume_response.status_code == 200
    resume_result = resume_response.json()
    assert resume_result["terminal_state"] == "running"
    assert resume_result["resumed"] is True
    assert resume_result["idempotent_replay"] is False

    continued_response = await _wait_for_terminal_run(
        api_client,
        run_id=accepted["run_id"],
        company_id=str(ids.company_id),
    )
    continued = continued_response.json()
    assert continued["stage"] == "interrupted"
    assert continued["terminal_state"] == "approval_required"
    assert continued["error_code"] == "human_approval_required"
    assert continued["missing_fields"] == []
    assert continued["pending_interrupt"]["kind"] == "approval"
    assert continued["pending_interrupt"]["context_hash"] == continued["context"][
        "analysis_signature"
    ]
    assert (
        continued["pending_interrupt"]["analysis_signature"]
        == scenario_response.json()["analysis_signature"]
    )
    assert continued["context"]["procurement_scenario_id"] == scenario_id
    assert continued["context"]["activity_record_ids"] == []
    assert continued["context"]["analysis_signature"] != pending["analysis_signature"]
    assert continued["telemetry"]["model_calls"] == 1
    assert continued["telemetry"]["tool_calls"] == 8
    assert continued["unsupported_items"] == []
    assert continued["approval_requirement"]["required"] is True

    continued_stream = await api_client.get(
        f"/api/runs/{accepted['run_id']}/events",
        params={"company_id": str(ids.company_id)},
    )
    assert continued_stream.status_code == 200
    assert continued_stream.text.count("event: run.resumed\n") == 1
    assert continued_stream.text.count("event: clarification.required\n") == 1
    assert continued_stream.text.count("event: run.stopped\n") == 2
    assert '"code":"human_approval_required"' in continued_stream.text
    assert "event: approval.required\n" in continued_stream.text

    approval_interrupt = continued["pending_interrupt"]
    decision_response = await api_client.post(
        f"/api/approvals/{approval_interrupt['approval_id']}/decision",
        json={
            "company_id": str(ids.company_id),
            "decision": "approve",
            "preview_hash": approval_interrupt["preview_hash"],
            "actor_id": str(ids.approver_id),
            "decision_note": "Approved by the synthetic E2E approver.",
        },
    )
    assert decision_response.status_code == 200
    assert decision_response.json()["status"] == "approved"

    approval_resume_payload = {
        "company_id": str(ids.company_id),
        "actor_id": str(ids.approver_id),
        "interrupt_id": approval_interrupt["interrupt_id"],
        "interrupt_sequence": approval_interrupt["sequence"],
        "analysis_signature": approval_interrupt["analysis_signature"],
        "idempotency_key": "synthetic-approval-resume-001",
        "approval": {
            "approval_id": approval_interrupt["approval_id"],
            "preview_hash": approval_interrupt["preview_hash"],
        },
    }
    approval_resume_response = await api_client.post(
        f"/api/runs/{accepted['run_id']}/resume",
        json=approval_resume_payload,
    )
    assert approval_resume_response.status_code == 200
    assert approval_resume_response.json()["terminal_state"] == "running"
    assert approval_resume_response.json()["resumed"] is True

    completed_response = await _wait_for_terminal_run(
        api_client,
        run_id=accepted["run_id"],
        company_id=str(ids.company_id),
    )
    completed = completed_response.json()
    assert completed["stage"] == "completed"
    assert completed["terminal_state"] == "success"
    assert completed["error_code"] is None
    assert completed["pending_interrupt"] is None
    assert completed["approval_requirement"]["required"] is False
    assert completed["telemetry"]["model_calls"] == 1
    assert completed["telemetry"]["tool_calls"] == 8

    completed_stream = await api_client.get(
        f"/api/runs/{accepted['run_id']}/events",
        params={"company_id": str(ids.company_id)},
    )
    assert completed_stream.status_code == 200
    assert completed_stream.text.count("event: run.resumed\n") == 2
    assert completed_stream.text.count("event: approval.required\n") == 1
    assert completed_stream.text.count("event: run.completed\n") == 1
    persisted_after_resume = completed_stream.text

    replay_response = await api_client.post(
        f"/api/runs/{accepted['run_id']}/resume",
        json=approval_resume_payload,
    )
    assert replay_response.status_code == 200
    replay = replay_response.json()
    assert replay["terminal_state"] == "success"
    assert replay["resumed"] is False
    assert replay["idempotent_replay"] is True

    stream_after_replay = await api_client.get(
        f"/api/runs/{accepted['run_id']}/events",
        params={"company_id": str(ids.company_id)},
    )
    assert stream_after_replay.status_code == 200
    assert stream_after_replay.text == persisted_after_resume
    assert stream_after_replay.text.count("event: run.resumed\n") == 2


@pytest.mark.asyncio
async def test_agent_no_feasible_option_stops_before_approval(
    api_client,
    e2e_context,
) -> None:
    ids = e2e_context.ids
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
            "quantity": "10000",
            "quantity_unit": "kg",
            "max_cost_increase_pct": "0",
            "max_lead_time_days": 0,
            "minimum_circularity_score": "100",
            "material_constraints": {
                "allowed_material_codes": ["RECYCLED-ALUMINIUM"]
            },
        },
    )
    assert scenario_response.status_code == 201
    scenario = scenario_response.json()
    assert scenario["terminal_state"] == "no_feasible_option"

    response = await api_client.post(
        "/api/agent/requests",
        json={
            "query": "Measure recycled aluminium emissions and recommend a feasible supplier.",
            "context": {
                "company_id": str(ids.company_id),
                "actor_id": str(ids.procurement_manager_id),
                "site_id": str(ids.site_id),
                "reporting_period_id": str(ids.reporting_period_id),
                "activity_record_ids": [str(ids.activity_record_id)],
                "procurement_scenario_id": scenario["id"],
                "material_scope": ["RECYCLED-ALUMINIUM"],
                "constraints": {
                    "max_cost_increase_pct": "0",
                    "max_lead_time_days": 0,
                    "minimum_circularity_score": "100",
                },
            },
        },
    )

    assert response.status_code == 202
    accepted = response.json()
    assert accepted["terminal_state"] == "running"
    terminal_response = await _wait_for_terminal_run(
        api_client,
        run_id=accepted["run_id"],
        company_id=str(ids.company_id),
    )
    run = terminal_response.json()

    assert run["workflow"] == "cross_module"
    assert run["stage"] == "stopped"
    assert run["terminal_state"] == "no_feasible_option"
    assert run["error_code"] == "no_feasible_supplier"
    assert run["recommendation"] is None
    assert run["approval_requirement"]["required"] is False
    assert run["unsupported_reason"] is None
    assert run["unsupported_items"] == []
    assert run["telemetry"]["provider"] == "gemini"
    assert run["telemetry"]["model_calls"] == 1
    assert run["telemetry"]["tool_calls"] == 6

    stream_response = await api_client.get(
        f"/api/runs/{accepted['run_id']}/events",
        params={"company_id": str(ids.company_id)},
    )
    assert stream_response.status_code == 200
    assert "event: tool.completed\n" in stream_response.text
    assert "event: tool.failed\n" in stream_response.text
    assert '"code":"no_feasible_supplier"' in stream_response.text
    assert '"tool_name":"load_supplier_candidates"' in stream_response.text
    assert "event: approval.required\n" not in stream_response.text
    assert "create_approval_preview" not in stream_response.text
    assert "event: run.stopped\n" in stream_response.text


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv(LIVE_LLM_E2E_ENV) != LIVE_LLM_E2E_OPT_IN,
    reason=(
        "live provider E2E requires explicit "
        f"{LIVE_LLM_E2E_ENV}={LIVE_LLM_E2E_OPT_IN} opt-in"
    ),
)
async def test_live_provider_runs_production_graph_with_real_approval_observer(
    api_client,
    e2e_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paid smoke test using the production graph and shared approval observer."""

    ids = e2e_context.ids
    previous_override = app.dependency_overrides[get_agent_run_service_factory]

    def production_service_factory_override():
        return get_agent_run_service_factory()

    app.dependency_overrides[
        get_agent_run_service_factory
    ] = production_service_factory_override
    try:
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
                "quantity": "10000",
                "quantity_unit": "kg",
                "max_cost_increase_pct": "5",
                "max_lead_time_days": 30,
                "minimum_circularity_score": "0",
                "material_constraints": {
                    "allowed_material_codes": ["RECYCLED-ALUMINIUM"]
                },
            },
        )
        assert scenario_response.status_code == 201
        scenario = scenario_response.json()

        accepted_response = await api_client.post(
            "/api/agent/requests",
            json={
                "query": (
                    "First measure the verified Plant B recycled aluminium emissions, then "
                    "recommend "
                    "the feasible lower-carbon supplier under the frozen constraints."
                ),
                "context": {
                    "company_id": str(ids.company_id),
                    "actor_id": str(ids.procurement_manager_id),
                    "site_id": str(ids.site_id),
                    "reporting_period_id": str(ids.reporting_period_id),
                    "carbon_measurement_id": str(ids.measurement_id),
                    "current_product_id": str(ids.current_product_id),
                    "method_definition_id": str(ids.scoring_method_id),
                    "activity_record_ids": [str(ids.activity_record_id)],
                    "procurement_scenario_id": scenario["id"],
                    "material_scope": ["RECYCLED-ALUMINIUM"],
                    "constraints": {
                        "max_cost_increase_pct": "5",
                        "max_lead_time_days": 30,
                        "minimum_circularity_score": "0",
                    },
                },
            },
        )
        assert accepted_response.status_code == 202
        run_id = accepted_response.json()["run_id"]
        interrupted_response = await _wait_for_terminal_run(
            api_client,
            run_id=run_id,
            company_id=str(ids.company_id),
        )
        interrupted = interrupted_response.json()
        configured_model = build_ai_model()
        diagnostic_stream = await api_client.get(
            f"/api/runs/{run_id}/events",
            params={"company_id": str(ids.company_id)},
        )
        tool_event_data = [
            line.removeprefix("data: ")
            for line in diagnostic_stream.text.splitlines()
            if line.startswith("data: ") and '"tool_name"' in line
        ]
        diagnostic = {
            "terminal_state": interrupted["terminal_state"],
            "error_code": interrupted["error_code"],
            "missing_fields": interrupted["missing_fields"],
            "planning_selection": interrupted["telemetry"]["planning_selection"],
            "pending_interrupt_kind": (
                interrupted["pending_interrupt"]["kind"]
                if interrupted["pending_interrupt"] is not None
                else None
            ),
            "tool_event_data": tool_event_data,
        }
        assert interrupted["terminal_state"] == "approval_required", diagnostic
        assert interrupted["telemetry"]["provider"] == configured_model.provider
        assert interrupted["telemetry"]["model_id"] == configured_model.model_id
        assert interrupted["telemetry"]["model_calls"] == 1
        assert interrupted["telemetry"]["planning_selection"]["modules"] == [
            "measurement",
            "procurement",
        ]

        pending = interrupted["pending_interrupt"]
        decision_response = await api_client.post(
            f"/api/approvals/{pending['approval_id']}/decision",
            json={
                "company_id": str(ids.company_id),
                "decision": "approve",
                "preview_hash": pending["preview_hash"],
                "actor_id": str(ids.approver_id),
                "decision_note": "Approved during the explicit paid live-provider smoke test.",
            },
        )
        assert decision_response.status_code == 200

        resume_response = await api_client.post(
            f"/api/runs/{run_id}/resume",
            json={
                "company_id": str(ids.company_id),
                "actor_id": str(ids.approver_id),
                "interrupt_id": pending["interrupt_id"],
                "interrupt_sequence": pending["sequence"],
                "analysis_signature": pending["analysis_signature"],
                "idempotency_key": "live-provider-approval-resume-001",
                "approval": {
                    "approval_id": pending["approval_id"],
                    "preview_hash": pending["preview_hash"],
                },
            },
        )
        assert resume_response.status_code == 200
        assert resume_response.json()["resumed"] is True

        completed_response = await _wait_for_terminal_run(
            api_client,
            run_id=run_id,
            company_id=str(ids.company_id),
        )
        completed = completed_response.json()
        assert completed["terminal_state"] == "success"
        assert completed["stage"] == "completed"

        stream_response = await api_client.get(
            f"/api/runs/{run_id}/events",
            params={"company_id": str(ids.company_id)},
        )
        assert stream_response.status_code == 200
        assert "event: provider.completed\n" in stream_response.text
        assert "event: approval.required\n" in stream_response.text
        assert "event: run.completed\n" in stream_response.text
    finally:
        app.dependency_overrides[get_agent_run_service_factory] = previous_override
