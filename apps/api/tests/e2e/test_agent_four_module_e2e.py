from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

import app.modules.agents.runtime as agent_runtime
from app.api.routes.agents import get_agent_run_service_factory
from app.db.models.assurance import DisclosureRequirement
from app.db.models.core import Approval
from app.db.models.dispatch import DispatchRecommendation
from app.main import app
from app.modules.agents.llm.contracts import AIRequest, AIResult, AITokenUsage
from app.modules.agents.planning import PlannerSelection, planning_system_instruction
from tests.e2e.conftest import E2EContext


class FourModuleE2EPlanner:
    """Select the frozen golden path without making a paid provider call."""

    provider = "gemini"
    model_id = "e2e-four-module-planner-v1"

    async def generate(self, request: AIRequest) -> AIResult:
        assert request.system_instruction == planning_system_instruction()
        selection = PlannerSelection(
            disposition="execute",
            modules=("measurement", "assurance", "procurement", "dispatch"),
            reason_code="synthetic_four_module_request",
        )
        return AIResult(
            provider=self.provider,
            model_id=self.model_id,
            text=selection.model_dump_json(),
            usage=AITokenUsage(input_tokens=20, output_tokens=8, total_tokens=28),
            provider_request_id="synthetic-four-module-planner-request",
            finish_reason="STOP",
            latency_ms=1,
        )


async def _wait_for_terminal_run(
    client: httpx.AsyncClient,
    *,
    run_id: str,
    company_id: str,
) -> dict[str, Any]:
    # The golden profile allows 45 seconds; a source-building run must be
    # allowed to use that budget before the test treats it as stuck.
    for _ in range(1000):
        response = await client.get(
            f"/api/runs/{run_id}",
            params={"company_id": company_id},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["terminal_state"] != "running":
            return payload
        await asyncio.sleep(0.05)
    pytest.fail("four-module agent run did not reach a terminal state")


async def _approve_and_resume(
    client: httpx.AsyncClient,
    *,
    run: dict[str, Any],
    company_id: str,
    approver_id: str,
    idempotency_key: str,
) -> None:
    pending = run["pending_interrupt"]
    resume_payload = {
        "company_id": company_id,
        "actor_id": approver_id,
        "interrupt_id": pending["interrupt_id"],
        "interrupt_sequence": pending["sequence"],
        "analysis_signature": pending["analysis_signature"],
        "idempotency_key": idempotency_key,
        "approval": {
            "approval_id": pending["approval_id"],
            "preview_hash": pending["preview_hash"],
        },
    }
    awaiting_decision = await client.post(
        f"/api/runs/{run['run_id']}/resume",
        json={**resume_payload, "idempotency_key": f"{idempotency_key}-pending"},
    )
    assert awaiting_decision.status_code == 200, awaiting_decision.text
    assert awaiting_decision.json()["resumed"] is False
    assert awaiting_decision.json()["terminal_state"] == "approval_required"
    detail = await client.get(
        f"/api/approvals/{pending['approval_id']}",
        params={"company_id": company_id},
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["preview_current"] is True
    decision = await client.post(
        f"/api/approvals/{pending['approval_id']}/decision",
        json={
            "company_id": company_id,
            "actor_id": approver_id,
            "decision": "approve",
            "preview_hash": pending["preview_hash"],
            "idempotency_key": detail.json()["idempotency_key"],
            "decision_note": "Reviewed the exact synthetic four-module preview.",
        },
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "approved"
    response = await client.post(
        f"/api/runs/{run['run_id']}/resume",
        json=resume_payload,
    )
    assert response.status_code == 200, response.text
    assert response.json()["resumed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "planner_mode",
    [
        "structured_fixture",
        pytest.param(
            "live",
            marks=pytest.mark.skipif(
                os.getenv("CARBONMESH_RUN_LIVE_LLM_E2E") != "paid-provider-call",
                reason="Live planner E2E requires explicit paid-provider-call opt-in.",
            ),
        ),
    ],
)
async def test_agent_composition_runs_four_modules_with_real_human_decisions(
    api_client: httpx.AsyncClient,
    e2e_context: E2EContext,
    monkeypatch: pytest.MonkeyPatch,
    planner_mode: str,
) -> None:
    ids = e2e_context.ids

    # Select the publishable supported subset without mutating the canonical
    # standard. The separate Assurance E2E still exercises all requirements and
    # proves that the prior-period claim remains visibly blocked.
    async with e2e_context.session_factory() as session:
        supported_requirement_ids = tuple(
            await session.scalars(
                select(DisclosureRequirement.id)
                .where(
                    DisclosureRequirement.company_id == ids.company_id,
                    DisclosureRequirement.requirement_code.in_(
                        ("S2-BOUNDARY", "S2-TOTAL")
                    ),
                )
                .order_by(DisclosureRequirement.sequence)
            )
        )
    assert len(supported_requirement_ids) == 2

    draft_response = await api_client.post(
        "/api/assurance/drafts",
        json={
            "company_id": str(ids.company_id),
            "standard_id": str(ids.assurance_standard_id),
            "site_id": str(ids.site_id),
            "reporting_period_id": str(ids.reporting_period_id),
            "measurement_id": str(ids.assurance_measurement_id),
            "agent_run_id": str(ids.assurance_agent_run_id),
            "requested_by": str(ids.analyst_id),
            "idempotency_key": "agent-four-module-supported-draft",
            "title": "Q3 2026 supported synthetic disclosure",
            "requirement_ids": [str(item) for item in supported_requirement_ids],
        },
    )
    assert draft_response.status_code == 201, draft_response.text
    draft_id = draft_response.json()["id"]

    procurement_response = await api_client.post(
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
    assert procurement_response.status_code == 201, procurement_response.text
    procurement_scenario_id = procurement_response.json()["id"]

    forecast_response = await api_client.post(
        "/api/dispatch/forecasts/sync",
        json={
            "company_id": str(ids.company_id),
            "site_id": str(ids.site_id),
            "zone": "IN",
            "horizon_hours": 24,
            "source_mode": "fixture",
        },
    )
    assert forecast_response.status_code == 200, forecast_response.text

    dispatch_response = await api_client.post(
        "/api/dispatch/scenarios",
        json={
            "company_id": str(ids.company_id),
            "site_id": str(ids.site_id),
            "flexible_load_id": str(ids.flexible_load_id),
            "method_definition_id": str(ids.dispatch_method_id),
            "policy_definition_id": str(ids.dispatch_policy_id),
            "forecast_source_document_id": forecast_response.json()[
                "source_document_id"
            ],
            "requested_by": str(ids.analyst_id),
            "window_start": "2026-10-01T08:00:00Z",
            "window_end": "2026-10-01T20:00:00Z",
            "baseline_start": "2026-10-01T08:00:00Z",
            "maximum_delay_minutes": 240,
        },
    )
    assert dispatch_response.status_code == 201, dispatch_response.text
    dispatch_scenario_id = dispatch_response.json()["id"]

    if planner_mode == "structured_fixture":
        monkeypatch.setattr(agent_runtime, "build_ai_model", FourModuleE2EPlanner)
    previous_service_override = app.dependency_overrides[get_agent_run_service_factory]

    def production_service_factory_override():
        return get_agent_run_service_factory()

    app.dependency_overrides[
        get_agent_run_service_factory
    ] = production_service_factory_override
    try:
        accepted_response = await api_client.post(
            "/api/agent/requests",
            json={
                "query": (
                    "Run Measurement, Assurance, Procurement, and advisory Dispatch "
                    "for Plant B, Q3 2026 using the frozen synthetic scenarios."
                ),
                "context": {
                    "company_id": str(ids.company_id),
                    "actor_id": str(ids.analyst_id),
                    "grid_source_mode": "fixture",
                    # Site and period are intentionally supplied only in natural
                    # language to exercise exact tenant-scoped resolution.
                    "current_product_id": str(ids.current_product_id),
                    "activity_record_ids": [str(ids.activity_record_id)],
                    "standard_id": str(ids.assurance_standard_id),
                    "disclosure_draft_id": draft_id,
                    "requirement_ids": [
                        str(item) for item in supported_requirement_ids
                    ],
                    "procurement_scenario_id": procurement_scenario_id,
                    "flexible_load_id": str(ids.flexible_load_id),
                    "dispatch_scenario_id": dispatch_scenario_id,
                    "material_scope": ["RECYCLED-ALUMINIUM"],
                    "constraints": {
                        "max_cost_increase_pct": "5",
                        "max_lead_time_days": 30,
                        "minimum_circularity_score": "0",
                    },
                    "dispatch_constraints": {
                        "window_start": "2026-10-01T08:00:00Z",
                        "window_end": "2026-10-01T20:00:00Z",
                        "duration_minutes": 120,
                        "max_delay_minutes": 240,
                        "maximum_power_kw": "500",
                    },
                },
            },
        )
        assert accepted_response.status_code == 202, accepted_response.text
        run_id = accepted_response.json()["run_id"]

        expected_targets = (
            "disclosure_draft",
            "procurement_recommendation",
            "dispatch_recommendation",
        )
        observed_tool_counts = []
        for index, target_type in enumerate(expected_targets, start=1):
            interrupted = await _wait_for_terminal_run(
                api_client,
                run_id=run_id,
                company_id=str(ids.company_id),
            )
            pending = interrupted["pending_interrupt"]
            disclosure_diagnostics = None
            if interrupted["terminal_state"] != "approval_required":
                disclosure_diagnostics = (
                    await api_client.get(
                        f"/api/assurance/drafts/{draft_id}",
                        params={"company_id": str(ids.company_id)},
                    )
                ).json()
                print(json.dumps(disclosure_diagnostics, indent=2))
            assert interrupted["terminal_state"] == "approval_required", {
                "terminal_state": interrupted["terminal_state"],
                "error_code": interrupted["error_code"],
                "message": interrupted["message"],
                "unsupported_items": interrupted["unsupported_items"],
                "tool_calls": interrupted["telemetry"]["tool_calls"],
                "graph_state": interrupted["telemetry"]["graph_state"],
                "disclosure": disclosure_diagnostics,
            }
            assert pending["target_type"] == target_type
            assert pending["context_hash"] == interrupted["context"][
                "analysis_signature"
            ]
            if target_type in {"disclosure_draft", "dispatch_recommendation"}:
                assert pending["approval_context_hash"] != pending["context_hash"]
            observed_tool_counts.append(interrupted["telemetry"]["tool_calls"])
            await _approve_and_resume(
                api_client,
                run=interrupted,
                company_id=str(ids.company_id),
                approver_id=str(ids.approver_id),
                idempotency_key=f"four-module-external-approval-{index}",
            )

        completed = await _wait_for_terminal_run(
            api_client,
            run_id=run_id,
            company_id=str(ids.company_id),
        )
        assert completed["terminal_state"] == "success"
        assert completed["stage"] == "completed"
        assert completed["workflow"] == "four_module"
        assert completed["context"]["site_id"] == str(ids.site_id)
        assert completed["context"]["reporting_period_id"] == str(
            ids.reporting_period_id
        )
        assert completed["telemetry"]["tool_calls"] == 17
        assert observed_tool_counts == [11, 14, 17]
        assert observed_tool_counts[1] - observed_tool_counts[0] == 3
        assert observed_tool_counts[2] - observed_tool_counts[1] == 3
        assert completed["telemetry"]["checkpoint"]["completed_modules"] == [
            "measurement",
            "assurance",
            "procurement",
            "dispatch",
        ]
        module_outcomes = {
            item["module"]: item["terminal_state"]
            for item in completed["telemetry"]["graph_state"]["module_outcomes"]
        }
        assert module_outcomes == {
            "measurement": "success",
            "assurance": "success",
            "procurement": "success",
            "dispatch": "success",
        }
        assert {item["metric_key"] for item in completed["facts"]} >= {
            "emissions.scope2.location_based",
            "emissions.scope3.category1",
            "procurement.projected_avoided_emissions",
            "dispatch.avoided_emissions",
        }

        stream_response = await api_client.get(
            f"/api/runs/{run_id}/events",
            params={"company_id": str(ids.company_id)},
        )
        assert stream_response.status_code == 200
        assert stream_response.text.count("event: approval.required\n") == 3
        assert stream_response.text.count("event: run.resumed\n") == 3
        assert stream_response.text.count("event: run.completed\n") == 1

        artifact_directory = os.getenv("CARBONMESH_AGENT_E2E_ARTIFACT_DIR")
        if artifact_directory:
            destination = Path(artifact_directory)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f"{planner_mode}-run.json").write_text(
                json.dumps(
                    {
                        "data_basis": "precreated synthetic measurement test artifacts",
                        "planner_mode": planner_mode,
                        "run": completed,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (destination / f"{planner_mode}-events.txt").write_text(
                stream_response.text, encoding="utf-8"
            )

        async with e2e_context.session_factory() as session:
            recommendation = await session.scalar(
                select(DispatchRecommendation).where(
                    DispatchRecommendation.company_id == ids.company_id
                )
            )
            assert recommendation is not None
            assert recommendation.actuation_authorized is False
            approvals = list(
                await session.scalars(
                    select(Approval).where(Approval.company_id == ids.company_id)
                )
            )
            assert len(approvals) == 3
            assert len({item.id for item in approvals}) == 3
            assert {(item.target_type, item.status) for item in approvals} == {
                ("disclosure_draft", "approved"),
                ("procurement_recommendation", "approved"),
                ("dispatch_recommendation", "approved"),
            }
            effective_target_ids = {
                item.target_id or item.recommendation_id for item in approvals
            }
            assert None not in effective_target_ids
            assert len(effective_target_ids) == 3
            assert all(len(item.preview_hash) == 64 for item in approvals)
            assert all(item.ledger_event_id is not None for item in approvals)
            assert all(item.decided_by == ids.approver_id for item in approvals)
    finally:
        app.dependency_overrides[
            get_agent_run_service_factory
        ] = previous_service_override
