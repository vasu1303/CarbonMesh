from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

pytest.importorskip("langgraph")

from app.modules.agents.graph_contracts import (
    ApprovalInterrupt,
    BudgetState,
    ClarificationInterrupt,
    ContextEnvelope,
    ExecutionPlan,
    GraphState,
    PlanStage,
    ToolInvocation,
    ToolName,
    ToolResult,
)
from app.modules.agents.graphs import compile_agent_graphs
from app.modules.agents.planning import PlannerSelection, build_execution_plan

HASH = "b" * 64


class FakeToolInvoker:
    def __init__(self, results: dict[ToolName, ToolResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[ToolInvocation] = []

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls.append(invocation)
        return self.results.get(invocation.tool_name, ToolResult())


class SequentialApprovalInvoker(FakeToolInvoker):
    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls.append(invocation)
        if invocation.tool_name != "create_approval_preview":
            return ToolResult()
        target_types = {
            "assurance": "disclosure_draft",
            "procurement": "procurement_recommendation",
            "dispatch": "dispatch_recommendation",
        }
        return ToolResult(
            status="approval_required",
            pending_interrupt=ApprovalInterrupt(
                approval_id=uuid4(),
                target_type=target_types[invocation.module],  # type: ignore[index]
                target_id=uuid4(),
                preview_hash="c" * 64,
                analysis_signature=HASH,
                context_hash=HASH,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
            code="human_approval_required",
        )


def _context(modules: tuple) -> ContextEnvelope:
    return ContextEnvelope(
        company_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        actor_id=uuid4(),
        actor_role="sustainability_analyst",
        modules=modules,
        metric_keys=("emissions.scope2.location_based",),
        request_hash=HASH,
        analysis_signature=HASH,
    )


async def _run(plan: ExecutionPlan, invoker: FakeToolInvoker, *, budget=None) -> GraphState:
    context = _context(plan.modules)
    state = GraphState.initial(
        run_id=uuid4(),
        context=context,
        plan=plan,
        budget=budget,
    )
    result = await compile_agent_graphs(invoker).orchestrator.ainvoke(state)
    return GraphState.model_validate(result)


@pytest.mark.asyncio
async def test_orchestrator_runs_only_planned_tools_and_finishes_successfully() -> None:
    plan = ExecutionPlan(
        profile="single",
        context_tools=("resolve_context",),
        stages=(
            PlanStage(
                stage_id="measurement.run",
                module="measurement",
                tool_names=(
                    "validate_activity",
                    "calculate_emissions",
                    "write_ledger_event",
                ),
            ),
        ),
    )
    invoker = FakeToolInvoker()

    result = await _run(plan, invoker)

    assert result.status == "success"
    assert result.terminal_state == "success"
    assert result.checkpoint.completed_modules == ("measurement",)
    assert [call.tool_name for call in invoker.calls] == [
        "resolve_context",
        "validate_activity",
        "calculate_emissions",
        "write_ledger_event",
    ]
    assert result.budget.tool_calls == 4


@pytest.mark.asyncio
async def test_golden_graph_preserves_four_module_order() -> None:
    plan = ExecutionPlan(
        profile="golden",
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
    )
    invoker = FakeToolInvoker()

    result = await _run(plan, invoker)

    assert result.status == "success"
    assert result.checkpoint.completed_modules == (
        "measurement",
        "assurance",
        "procurement",
        "dispatch",
    )
    assert [call.tool_name for call in invoker.calls] == [
        "resolve_context",
        "validate_activity",
        "map_standard_requirement",
        "load_supplier_candidates",
        "sync_grid_forecast",
    ]


@pytest.mark.asyncio
async def test_four_module_graph_interrupts_for_all_three_bounded_approvals() -> None:
    plan = build_execution_plan(
        PlannerSelection(
            disposition="execute",
            modules=("measurement", "assurance", "procurement", "dispatch"),
            reason_code="four_module_golden_path",
        )
    )
    invoker = SequentialApprovalInvoker()
    graph = compile_agent_graphs(invoker).orchestrator
    state = GraphState.initial(
        run_id=uuid4(),
        context=_context(plan.modules),
        plan=plan,
    )
    interrupted_modules: list[str] = []
    interrupted_tool_counts: list[int] = []

    for _ in range(3):
        state = GraphState.model_validate(await graph.ainvoke(state))
        assert state.status == "interrupted"
        transition = next(
            item for item in reversed(state.transitions) if item.status == "interrupted"
        )
        assert transition.module is not None
        interrupted_modules.append(transition.module)
        interrupted_tool_counts.append(len(invoker.calls))
        completed_nodes = state.checkpoint.completed_nodes + (transition.node_name,)
        checkpoint = state.checkpoint.model_copy(
            update={
                "revision": state.checkpoint.revision + 1,
                "completed_nodes": completed_nodes,
                "pending_interrupt": None,
            }
        )
        state = GraphState.model_validate(
            state.model_copy(
                update={
                    "checkpoint": checkpoint,
                    "status": "running",
                    "terminal_state": None,
                    "pending_interrupt": None,
                    "error_code": None,
                }
            ).model_dump(mode="python")
        )

    state = GraphState.model_validate(await graph.ainvoke(state))

    assert interrupted_modules == ["assurance", "procurement", "dispatch"]
    assert state.status == "success"
    assert state.checkpoint.completed_modules == (
        "measurement",
        "assurance",
        "procurement",
        "dispatch",
    )
    assert {
        outcome.module: outcome.terminal_state for outcome in state.module_outcomes
    } == {
        "measurement": "success",
        "assurance": "success",
        "procurement": "success",
        "dispatch": "success",
    }
    assert interrupted_tool_counts == [11, 14, 17]
    assert state.budget.tool_calls == 17
    assert len(invoker.calls) == 17


@pytest.mark.asyncio
async def test_clarification_interrupt_stops_before_any_specialist_tool() -> None:
    interrupt = ClarificationInterrupt(
        code="context.site_required",
        message="Select a site before continuing.",
        required_fields=("context.site_id",),
        context_hash=HASH,
    )
    invoker = FakeToolInvoker(
        {
            "resolve_context": ToolResult(
                status="needs_clarification",
                pending_interrupt=interrupt,
                code="context_incomplete",
            )
        }
    )
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

    result = await _run(plan, invoker)

    assert result.status == "interrupted"
    assert result.terminal_state == "needs_clarification"
    assert result.pending_interrupt == interrupt
    assert [call.tool_name for call in invoker.calls] == ["resolve_context"]


@pytest.mark.asyncio
async def test_approval_interrupt_is_exact_and_does_not_commit_a_decision() -> None:
    approval = ApprovalInterrupt(
        approval_id=uuid4(),
        target_type="procurement_recommendation",
        target_id=uuid4(),
        preview_hash="c" * 64,
        analysis_signature=HASH,
        context_hash=HASH,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    invoker = FakeToolInvoker(
        {
            "create_approval_preview": ToolResult(
                status="approval_required",
                pending_interrupt=approval,
                code="human_approval_required",
            )
        }
    )
    plan = ExecutionPlan(
        profile="single",
        context_tools=(),
        stages=(
            PlanStage(
                stage_id="procurement.run",
                module="procurement",
                tool_names=(
                    "load_supplier_candidates",
                    "build_procurement_recommendation",
                    "create_approval_preview",
                ),
                requires_human_approval=True,
            ),
        ),
    )

    result = await _run(plan, invoker)

    assert result.status == "interrupted"
    assert result.terminal_state == "approval_required"
    assert result.pending_interrupt == approval
    assert [call.tool_name for call in invoker.calls][-1] == "create_approval_preview"
    assert all(call.tool_name != "decide_approval" for call in invoker.calls)


@pytest.mark.asyncio
async def test_approval_preview_success_without_interrupt_fails_closed() -> None:
    plan = ExecutionPlan(
        profile="single",
        context_tools=(),
        stages=(
            PlanStage(
                stage_id="procurement.run",
                module="procurement",
                tool_names=("create_approval_preview",),
                requires_human_approval=True,
            ),
        ),
    )
    invoker = FakeToolInvoker(
        {"create_approval_preview": ToolResult(status="success")}
    )

    result = await _run(plan, invoker)

    assert result.status == "stopped"
    assert result.terminal_state == "validation_failed"
    assert result.error_code == "approval_interrupt_missing"
    assert result.checkpoint.completed_modules == ()


@pytest.mark.asyncio
async def test_budget_exhaustion_fails_before_invoking_a_tool() -> None:
    plan = ExecutionPlan(
        profile="single",
        stages=(PlanStage(stage_id="measurement.run", module="measurement"),),
    )
    exhausted = BudgetState.for_profile("single").model_copy(update={"tool_calls": 8})
    invoker = FakeToolInvoker()

    result = await _run(plan, invoker, budget=exhausted)

    assert result.status == "stopped"
    assert result.terminal_state == "budget_exhausted"
    assert result.error_code == "tool_budget_exhausted"
    assert invoker.calls == []
