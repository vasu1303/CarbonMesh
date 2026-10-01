from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest


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
async def test_agent_query_run_and_terminal_sse_replay(api_client, e2e_context) -> None:
    ids = e2e_context.ids
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
    assert run["stage"] == "completed"
    assert run["terminal_state"] == "completed"
    assert {fact["metric_key"] for fact in run["facts"]} == {
        "emissions.scope3.category1",
        "procurement.projected_avoided_emissions",
        "procurement.cost_delta_pct",
    }
    assert run["recommendation"]["recommended_product_id"] == str(ids.recommended_product_id)
    assert run["recommendation"]["avoided_kgco2e"] == "62000.000000"
    assert run["recommendation"]["cost_delta_pct"] == "4.0000"
    assert run["approval_requirement"]["required"] is True
    assert run["approval_requirement"]["approval_id"] is not None
    assert run["telemetry"]["provider"] == "none"
    assert run["telemetry"]["model_calls"] == 0
    assert run["telemetry"]["tool_calls"] == 6
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
    assert "event: tool.completed\n" in stream_response.text
    assert "event: fact.created\n" in stream_response.text
    assert "event: approval.required\n" in stream_response.text
    assert "event: run.completed\n" in stream_response.text
    assert "event: run.stopped\n" not in stream_response.text
    assert stream_response.text.endswith("\n\n")

    final_sequence = run["telemetry"]["events"][-1]["sequence"]
    resumed = await api_client.get(
        f"/api/runs/{accepted['run_id']}/events",
        params={"company_id": str(ids.company_id)},
        headers={"Last-Event-ID": str(final_sequence - 1)},
    )

    assert resumed.status_code == 200
    assert resumed.text.count("event: run.completed\n") == 1
    assert "event: run.started\n" not in resumed.text


@pytest.mark.asyncio
async def test_agent_no_feasible_run_omits_unexecuted_approval_tool(
    api_client,
    e2e_context,
) -> None:
    ids = e2e_context.ids
    response = await api_client.post(
        "/api/agent/requests",
        json={
            "query": "Measure recycled aluminium emissions and recommend a feasible supplier.",
            "context": {
                "company_id": str(ids.company_id),
                "actor_id": str(ids.procurement_manager_id),
                "site_id": str(ids.site_id),
                "reporting_period_id": str(ids.reporting_period_id),
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

    assert run["terminal_state"] == "no_feasible_option"
    assert run["recommendation"] is None
    completed_tool_ids = {
        event["data"]["tool_id"]
        for event in run["telemetry"]["events"]
        if event["name"] == "tool.completed"
    }
    assert completed_tool_ids == {"T01", "T03", "T09", "T10", "T11"}
    assert "T12" not in completed_tool_ids
    assert run["telemetry"]["tool_calls"] == 5
