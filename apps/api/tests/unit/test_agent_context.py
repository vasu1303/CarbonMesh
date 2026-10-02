from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.modules.agents.context import (
    build_frozen_context,
    extract_query_context_hints,
)
from app.modules.agents.runtime import _preserves_frozen_context
from app.modules.agents.schemas import AgentContextRequest, ClarificationResume


@pytest.mark.asyncio
async def test_followup_reuses_one_scoped_context_and_changes_signature():
    from app.modules.agents.repository import ContextReferenceNotFoundError
    from app.modules.agents.runtime import GraphAgentRunService
    from app.modules.agents.schemas import AgentQueryRequest
    from tests.unit.test_agent_runtime_evaluations import FakeRuntimeRepository

    repository = FakeRuntimeRepository()
    service = GraphAgentRunService(repository)
    context = AgentContextRequest(company_id=uuid4(), actor_id=uuid4(), site_id=uuid4(),
                                  reporting_period_id=uuid4(), grid_source_mode="fixture",
                                  procurement_scenario_id=uuid4(),
                                  constraints={"max_cost_increase_pct": "5", "max_lead_time_days": 30})
    original = await service.start(AgentQueryRequest(query="Compare procurement", context=context), trace_id="test-original")
    prior = repository.runs[original.run_id]
    prior.terminal_state = "success"
    followup = AgentQueryRequest(query="Use the same plant with a tighter cost constraint",
                                previous_run_id=original.run_id,
                                context=AgentContextRequest(company_id=context.company_id, actor_id=context.actor_id,
                                                            constraints={"max_cost_increase_pct": "2"}))
    resolved = await service._resolve_previous_context(followup)
    assert resolved.context.site_id == context.site_id
    assert resolved.context.grid_source_mode == "fixture"
    assert resolved.context.constraints.max_lead_time_days == 30
    assert resolved.context.constraints.max_cost_increase_pct == 2
    assert resolved.context.procurement_scenario_id is None
    accepted = await service.start(followup, trace_id="test-followup")
    assert repository.runs[accepted.run_id].context_hash != prior.context_hash
    assert "previous_run_id" not in repository.runs[accepted.run_id].context_envelope
    for changed in ({"company_id": uuid4()}, {"actor_id": uuid4()}):
        with pytest.raises(ContextReferenceNotFoundError):
            await service._resolve_previous_context(followup.model_copy(update={
                "context": followup.context.model_copy(update=changed)}))


def test_planner_projection_is_bounded_and_contains_catalog_and_budgets():
    import json

    from app.modules.agents.planning import planning_user_content

    content = planning_user_content("Calculate emissions", available_context_fields=["site_id"],
                                    semantic_catalog={"metrics": [{"key": "emissions.scope2.location_based", "unit": "kgCO2e"}]})
    projection = json.loads(content.split("application_context=", 1)[1].split("\nrequest=", 1)[0])
    assert projection["budgets"]["golden"]["tool_calls"] == 20
    assert projection["semantic_catalog"]["metrics"][0]["unit"] == "kgCO2e"


def test_exact_demo_site_and_quarter_are_extracted_without_fuzzy_inference() -> None:
    hints = extract_query_context_hints(
        "Run the four-module workflow for Plant B, Q3 2026."
    )

    assert hints.site_reference == "plant-b"
    assert hints.period_start == date(2026, 7, 1)
    assert hints.period_end == date(2026, 9, 30)


def test_year_first_quarter_and_quarter_four_boundaries_are_deterministic() -> None:
    hints = extract_query_context_hints("Review Plant B for 2026-Q4")

    assert hints.period_start == date(2026, 10, 1)
    assert hints.period_end == date(2026, 12, 31)


def test_clarified_query_is_normalized_but_not_part_of_frozen_context() -> None:
    resume = ClarificationResume(
        context=AgentContextRequest(company_id=uuid4(), actor_id=uuid4()),
        clarified_query="  Measure   and assure Plant B. ",
    )

    assert resume.clarified_query == "Measure and assure Plant B."
    assert "clarified_query" not in resume.context.model_dump()


def test_clarification_cannot_replace_live_grid_with_fixture() -> None:
    request = AgentContextRequest(company_id=uuid4(), actor_id=uuid4())
    frozen = build_frozen_context(
        request_context=request, actor_role="operations_planner",
        query="Plan dispatch.", workflow="dispatch",
    )
    assert _preserves_frozen_context(frozen, request)
    assert not _preserves_frozen_context(
        frozen, request.model_copy(update={"grid_source_mode": "fixture"}),
    )
