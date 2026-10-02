from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.agents.graph_contracts import (
    BUDGET_PROFILES,
    MODULE_ORDER,
    TOOL_NAMES,
    BudgetExhaustedError,
    BudgetState,
    ContextConstraint,
    ContextEnvelope,
    ExecutionPlan,
    GraphState,
    PlanStage,
    budget_limits_for,
)
from app.modules.agents.planning import (
    INTENTIONALLY_UNPLANNED_TOOLS,
    PlannerSelection,
    build_execution_plan,
)

HASH = "a" * 64


def _context(modules=("measurement",)) -> ContextEnvelope:
    return ContextEnvelope(
        company_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        actor_id=uuid4(),
        actor_role="sustainability_analyst",
        modules=modules,
        metric_keys=("emissions.scope2.location_based",),
        constraints=(ContextConstraint(key="cost.max_increase_pct", value="5"),),
        request_hash=HASH,
        analysis_signature=HASH,
    )


def test_frozen_tool_catalogue_contains_exactly_the_24_approved_names() -> None:
    assert len(TOOL_NAMES) == 24
    assert len(set(TOOL_NAMES)) == 24
    assert TOOL_NAMES == (
        "resolve_context",
        "resolve_entity",
        "retrieve_ledger_facts",
        "retrieve_evidence",
        "write_ledger_event",
        "create_approval_preview",
        "validate_activity",
        "normalize_unit",
        "sync_grid_history",
        "select_emission_factor",
        "calculate_emissions",
        "calculate_confidence",
        "map_standard_requirement",
        "decompose_claim",
        "bind_claim_facts",
        "validate_citations",
        "detect_evidence_gaps",
        "load_supplier_candidates",
        "score_supplier",
        "calculate_procurement_impact",
        "build_procurement_recommendation",
        "sync_grid_forecast",
        "optimize_dispatch_window",
        "calculate_dispatch_impact",
    )


def test_budget_profiles_and_per_stage_repair_limits_are_exact() -> None:
    assert BUDGET_PROFILES["single"].model_dump() == {
        "max_model_calls": 3,
        "max_tool_calls": 8,
        "max_repairs_per_stage": 1,
        "target_latency_ms": 15_000,
    }
    assert BUDGET_PROFILES["golden"].model_dump() == {
        "max_model_calls": 6,
        "max_tool_calls": 20,
        "max_repairs_per_stage": 1,
        "target_latency_ms": 45_000,
    }
    assert BUDGET_PROFILES["resume"].model_dump() == {
        "max_model_calls": 1,
        "max_tool_calls": 3,
        "max_repairs_per_stage": 0,
        "target_latency_ms": 10_000,
    }

    single = BudgetState.for_profile("single").consume_repair("planner")
    with pytest.raises(BudgetExhaustedError, match="repair"):
        single.consume_repair("planner")
    with pytest.raises(BudgetExhaustedError, match="repair"):
        BudgetState.for_profile("resume").consume_repair("resume")


def test_execution_plan_enforces_module_dependency_order_and_tool_allowlists() -> None:
    plan = ExecutionPlan(
        profile="golden",
        context_tools=("resolve_context",),
        stages=(
            PlanStage(
                stage_id="measurement.run",
                module="measurement",
                tool_names=("validate_activity",),
            ),
            PlanStage(
                stage_id="assurance.run",
                module="assurance",
                depends_on=("measurement",),
                tool_names=("map_standard_requirement",),
            ),
            PlanStage(
                stage_id="procurement.run",
                module="procurement",
                depends_on=("measurement",),
                tool_names=("load_supplier_candidates",),
            ),
            PlanStage(
                stage_id="dispatch.run",
                module="dispatch",
                depends_on=("measurement",),
                tool_names=("sync_grid_forecast",),
            ),
        ),
        expected_model_calls=2,
    )

    assert plan.modules == MODULE_ORDER
    assert len(plan.plan_hash) == 64

    with pytest.raises(ValidationError, match="outside its allowlist"):
        PlanStage(
            stage_id="measurement.invalid",
            module="measurement",
            tool_names=("score_supplier",),
        )
    with pytest.raises(ValidationError, match="golden profile"):
        ExecutionPlan(
            profile="golden",
            stages=(
                PlanStage(stage_id="measurement.only", module="measurement"),
            ),
        )


def test_contracts_are_strict_frozen_and_checkpoint_binds_context_and_plan() -> None:
    context = _context()
    plan = ExecutionPlan(
        profile="single",
        stages=(
            PlanStage(
                stage_id="measurement.run",
                module="measurement",
                tool_names=("validate_activity",),
            ),
        ),
    )
    state = GraphState.initial(run_id=uuid4(), context=context, plan=plan)

    assert state.checkpoint.context_hash == context.analysis_signature
    assert state.checkpoint.plan_hash == plan.plan_hash
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ContextEnvelope.model_validate(
            {
                **context.model_dump(mode="python"),
                "unexpected": "rejected",
            }
        )
    with pytest.raises(ValidationError, match="frozen"):
        context.actor_role = "system"  # type: ignore[misc]


def test_four_module_plan_previews_every_consequential_module_within_budget() -> None:
    selection = PlannerSelection(
        disposition="execute",
        modules=MODULE_ORDER,
        reason_code="four_module_golden_path",
    )

    plan = build_execution_plan(selection)

    assert len(plan.context_tools) + sum(len(stage.tool_names) for stage in plan.stages) == 17
    assert {
        stage.module
        for stage in plan.stages
        if stage.requires_human_approval
    } == {"assurance", "procurement", "dispatch"}
    assert all(
        "create_approval_preview" in stage.tool_names
        for stage in plan.stages
        if stage.module in {"assurance", "procurement", "dispatch"}
    )
    assurance = plan.stage_for("assurance")
    assert assurance is not None
    assert "decompose_claim" in assurance.tool_names
    procurement = plan.stage_for("procurement")
    dispatch = plan.stage_for("dispatch")
    assert procurement is not None
    assert dispatch is not None
    assert procurement.tool_names == (
        "load_supplier_candidates",
        "build_procurement_recommendation",
        "create_approval_preview",
    )
    assert dispatch.tool_names == (
        "optimize_dispatch_window",
        "calculate_dispatch_impact",
        "create_approval_preview",
    )


def test_named_four_module_plan_uses_entity_verification_and_bounded_resumes() -> None:
    plan = build_execution_plan(
        PlannerSelection(
            disposition="execute",
            modules=MODULE_ORDER,
            reason_code="named_four_module_path",
        ),
        include_entity_resolution=True,
    )

    assert plan.context_tools == ("resolve_context", "resolve_entity")
    assert len(plan.context_tools) + sum(len(stage.tool_names) for stage in plan.stages) == 17
    measurement = plan.stage_for("measurement")
    assert measurement is not None
    assert "calculate_emissions" in measurement.tool_names
    assert "calculate_confidence" not in measurement.tool_names


def test_each_frozen_tool_is_reachable_or_has_a_fail_closed_justification() -> None:
    plans = [
        build_execution_plan(
            PlannerSelection(
                disposition="execute",
                modules=(module,),
                reason_code=f"{module}_request",
            )
        )
        for module in MODULE_ORDER
    ]
    plans.append(
        build_execution_plan(
            PlannerSelection(
                disposition="execute",
                modules=("measurement",),
                reason_code="named_measurement_request",
            ),
            include_entity_resolution=True,
        )
    )
    reachable = {
        tool
        for plan in plans
        for tool in (
            *plan.context_tools,
            *(tool for stage in plan.stages for tool in stage.tool_names),
        )
    }

    assert reachable | set(INTENTIONALLY_UNPLANNED_TOOLS) == set(TOOL_NAMES)
    assert set(TOOL_NAMES) - reachable == {
        "sync_grid_history",
        "write_ledger_event",
    }
    assert all(INTENTIONALLY_UNPLANNED_TOOLS.values())
    assert all(
        len(plan.context_tools) + sum(len(stage.tool_names) for stage in plan.stages)
        <= budget_limits_for(plan.profile).max_tool_calls
        for plan in plans
    )

