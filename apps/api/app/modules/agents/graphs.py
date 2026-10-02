"""Acyclic LangGraph definitions for bounded CarbonMesh orchestration.

Every executable node either records a control-flow transition or calls the
injected :class:`ToolInvoker`.  Business formulas, SQL, scoring, optimization,
hash creation, and approval decisions are intentionally absent from this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.modules.agents.graph_contracts import (
    CONTEXT_TOOL_ORDER,
    MODULE_ORDER,
    MODULE_TOOL_ORDER,
    AgentGraphState,
    BudgetExhaustedError,
    GraphName,
    GraphState,
    GraphTransition,
    ModuleName,
    ModuleOutcome,
    PendingInterrupt,
    TerminalState,
    ToolCallRecord,
    ToolInvocation,
    ToolInvoker,
    ToolInvokerError,
    ToolName,
    ToolOutput,
    ToolResult,
)

_KEEP = object()


@dataclass(frozen=True, slots=True)
class CompiledAgentGraphs:
    orchestrator: CompiledStateGraph
    measurement: CompiledStateGraph
    assurance: CompiledStateGraph
    procurement: CompiledStateGraph
    dispatch: CompiledStateGraph


def _state(value: GraphState | dict[str, Any]) -> GraphState:
    if isinstance(value, AgentGraphState):
        return value
    return AgentGraphState.model_validate(value)


def _transition(
    state: GraphState,
    *,
    graph_name: GraphName,
    node_name: str,
    kind: str,
    status: str,
    module: ModuleName | None = None,
    tool_name: ToolName | None = None,
    terminal_state: TerminalState | None = None,
    code: str | None = None,
    offset: int = 1,
) -> GraphTransition:
    return GraphTransition.model_validate(
        {
            "sequence": len(state.transitions) + offset,
            "graph_name": graph_name,
            "node_name": node_name,
            "kind": kind,
            "status": status,
            "module": module,
            "tool_name": tool_name,
            "terminal_state": terminal_state,
            "code": code,
        }
    )


def _next_module(state: GraphState, completed: ModuleName) -> ModuleName | None:
    modules = state.plan.modules
    position = modules.index(completed)
    return modules[position + 1] if position + 1 < len(modules) else None


def _update(
    state: GraphState,
    *,
    transitions: tuple[GraphTransition, ...],
    node_name: str,
    node_completed: bool,
    completed_module: ModuleName | None = None,
    next_module: ModuleName | None | object = _KEEP,
    pending_interrupt: PendingInterrupt | None | object = _KEEP,
    status: str | object = _KEEP,
    terminal_state: TerminalState | None | object = _KEEP,
    active_module: ModuleName | None | object = _KEEP,
    budget: object = _KEEP,
    tool_record: ToolCallRecord | None = None,
    module_outcome: ModuleOutcome | None = None,
    facts: tuple = (),
    unsupported_items: tuple = (),
    error_code: str | None | object = _KEEP,
) -> dict[str, Any]:
    combined_transitions = state.transitions + transitions
    completed_nodes = state.checkpoint.completed_nodes
    if node_completed and node_name not in completed_nodes:
        completed_nodes += (node_name,)
    completed_modules = state.checkpoint.completed_modules
    if completed_module is not None and completed_module not in completed_modules:
        completed_modules += (completed_module,)

    checkpoint_update: dict[str, Any] = {
        "revision": state.checkpoint.revision + 1,
        "completed_nodes": completed_nodes,
        "completed_modules": completed_modules,
        "last_transition_sequence": len(combined_transitions),
    }
    if next_module is not _KEEP:
        checkpoint_update["next_module"] = next_module
    if pending_interrupt is not _KEEP:
        checkpoint_update["pending_interrupt"] = pending_interrupt
    checkpoint = state.checkpoint.model_copy(update=checkpoint_update)

    update: dict[str, Any] = {
        "checkpoint": checkpoint,
        "transitions": combined_transitions,
    }
    if status is not _KEEP:
        update["status"] = status
    if terminal_state is not _KEEP:
        update["terminal_state"] = terminal_state
    if active_module is not _KEEP:
        update["active_module"] = active_module
    if pending_interrupt is not _KEEP:
        update["pending_interrupt"] = pending_interrupt
    if budget is not _KEEP:
        update["budget"] = budget
    if tool_record is not None:
        update["tool_records"] = state.tool_records + (tool_record,)
    if module_outcome is not None:
        existing_modules = tuple(item.module for item in state.module_outcomes)
        if module_outcome.module in existing_modules:
            # An approval interrupt is a durable intermediate outcome.  Once the
            # approved checkpoint continues, replace that outcome with the
            # module's final result instead of leaving ``approval_required`` in
            # an otherwise successful run.
            update["module_outcomes"] = tuple(
                module_outcome if item.module == module_outcome.module else item
                for item in state.module_outcomes
            )
        else:
            update["module_outcomes"] = state.module_outcomes + (module_outcome,)
    if facts:
        update["facts"] = state.facts + tuple(fact for fact in facts if fact not in state.facts)
    if unsupported_items:
        update["unsupported_items"] = state.unsupported_items + tuple(
            item for item in unsupported_items if item not in state.unsupported_items
        )
    if error_code is not _KEEP:
        update["error_code"] = error_code
    return update


def _already_completed(state: GraphState, node_name: str) -> bool:
    return node_name in state.checkpoint.completed_nodes


def _is_halted(state: GraphState) -> bool:
    return state.status in {"success", "stopped", "interrupted"}


def _prior_outputs(state: GraphState) -> tuple[ToolOutput, ...]:
    outputs = tuple(output for record in state.tool_records for output in record.outputs)
    return outputs[-200:]


def _planned_tool(
    state: GraphState,
    *,
    module: ModuleName | None,
    tool_name: ToolName,
) -> tuple[bool, str]:
    if module is None:
        return tool_name in state.plan.context_tools, "context"
    stage = state.plan.stage_for(module)
    if stage is None:
        return False, module
    return tool_name in stage.tool_names, stage.stage_id


def _terminal_update(
    state: GraphState,
    *,
    graph_name: GraphName,
    node_name: str,
    module: ModuleName | None,
    tool_name: ToolName,
    terminal_state: TerminalState,
    code: str,
    budget: object = _KEEP,
    tool_record: ToolCallRecord | None = None,
    pending_interrupt: PendingInterrupt | None = None,
    node_completed: bool = False,
    facts: tuple = (),
    unsupported_items: tuple = (),
) -> dict[str, Any]:
    interrupted = terminal_state in {"needs_clarification", "approval_required"}
    transition = _transition(
        state,
        graph_name=graph_name,
        node_name=node_name,
        kind="interrupt" if interrupted else "validation",
        status="interrupted" if interrupted else "blocked",
        module=module,
        tool_name=tool_name,
        terminal_state=terminal_state,
        code=code,
    )
    outcome = (
        ModuleOutcome(module=module, terminal_state=terminal_state, code=code)
        if module is not None
        else None
    )
    return _update(
        state,
        transitions=(transition,),
        node_name=node_name,
        node_completed=node_completed,
        pending_interrupt=pending_interrupt,
        status="interrupted" if interrupted else "stopped",
        terminal_state=terminal_state,
        budget=budget,
        tool_record=tool_record,
        module_outcome=outcome,
        facts=facts,
        unsupported_items=unsupported_items,
        error_code=code,
    )


def _make_tool_node(
    *,
    graph_name: GraphName,
    module: ModuleName | None,
    tool_name: ToolName,
    invoker: ToolInvoker,
):
    node_name = f"{graph_name}.{tool_name}"

    async def invoke_tool(raw_state: GraphState) -> dict[str, Any]:
        state = _state(raw_state)
        if _already_completed(state, node_name) or _is_halted(state):
            return {}

        planned, stage_id = _planned_tool(state, module=module, tool_name=tool_name)
        if not planned:
            skipped = _transition(
                state,
                graph_name=graph_name,
                node_name=node_name,
                kind="tool",
                status="skipped",
                module=module,
                tool_name=tool_name,
            )
            return _update(
                state,
                transitions=(skipped,),
                node_name=node_name,
                node_completed=True,
            )

        try:
            budget = state.budget.consume_tool_call()
        except BudgetExhaustedError:
            return _terminal_update(
                state,
                graph_name=graph_name,
                node_name=node_name,
                module=module,
                tool_name=tool_name,
                terminal_state="budget_exhausted",
                code="tool_budget_exhausted",
            )

        started = _transition(
            state,
            graph_name=graph_name,
            node_name=node_name,
            kind="tool",
            status="started",
            module=module,
            tool_name=tool_name,
        )
        invocation = ToolInvocation(
            run_id=state.run_id,
            graph_name=graph_name,
            module=module,
            stage_id=stage_id,
            tool_name=tool_name,
            context=state.context,
            prior_outputs=_prior_outputs(state),
            checkpoint_revision=state.checkpoint.revision,
        )
        try:
            raw_result = await invoker.invoke(invocation)
            result = (
                raw_result
                if isinstance(raw_result, ToolResult)
                else ToolResult.model_validate(raw_result)
            )
        except ToolInvokerError as error:
            record = ToolCallRecord(
                graph_name=graph_name,
                module=module,
                stage_id=stage_id,
                tool_name=tool_name,
                status=error.terminal_state,
                code=cast(Any, error.code),
            )
            failed_state = state.model_copy(update={"transitions": state.transitions + (started,)})
            return _terminal_update(
                failed_state,
                graph_name=graph_name,
                node_name=node_name,
                module=module,
                tool_name=tool_name,
                terminal_state=error.terminal_state,
                code=error.code,
                budget=budget,
                tool_record=record,
            )
        except Exception:  # noqa: BLE001 - graph boundary fails closed with a safe code
            record = ToolCallRecord(
                graph_name=graph_name,
                module=module,
                stage_id=stage_id,
                tool_name=tool_name,
                status="validation_failed",
                code="tool_invocation_failed",
            )
            failed_state = state.model_copy(update={"transitions": state.transitions + (started,)})
            return _terminal_update(
                failed_state,
                graph_name=graph_name,
                node_name=node_name,
                module=module,
                tool_name=tool_name,
                terminal_state="validation_failed",
                code="tool_invocation_failed",
                budget=budget,
                tool_record=record,
            )

        if tool_name == "create_approval_preview" and result.status == "success":
            result = result.model_copy(
                update={
                    "status": "validation_failed",
                    "code": "approval_interrupt_missing",
                }
            )

        record = ToolCallRecord(
            graph_name=graph_name,
            module=module,
            stage_id=stage_id,
            tool_name=tool_name,
            status=result.status,
            outputs=result.outputs,
            rows_processed=result.rows_processed,
            evidence_chunks_retrieved=result.evidence_chunks_retrieved,
            cache_hit=result.cache_hit,
            code=result.code,
        )
        if result.status != "success":
            failed_state = state.model_copy(update={"transitions": state.transitions + (started,)})
            return _terminal_update(
                failed_state,
                graph_name=graph_name,
                node_name=node_name,
                module=module,
                tool_name=tool_name,
                terminal_state=result.status,
                code=result.code or result.status,
                budget=budget,
                tool_record=record,
                pending_interrupt=result.pending_interrupt,
                facts=result.facts,
                unsupported_items=result.unsupported_items,
            )

        completed = _transition(
            state,
            graph_name=graph_name,
            node_name=node_name,
            kind="tool",
            status="completed",
            module=module,
            tool_name=tool_name,
            offset=2,
        )
        return _update(
            state,
            transitions=(started, completed),
            node_name=node_name,
            node_completed=True,
            budget=budget,
            tool_record=record,
            facts=result.facts,
            unsupported_items=result.unsupported_items,
        )

    invoke_tool.__name__ = node_name.replace(".", "_")
    return invoke_tool


def _make_module_entry(module: ModuleName):
    node_name = f"{module}.enter"

    async def enter(raw_state: GraphState) -> dict[str, Any]:
        state = _state(raw_state)
        if _already_completed(state, node_name) or _is_halted(state):
            return {}
        stage = state.plan.stage_for(module)
        transition = _transition(
            state,
            graph_name=module,
            node_name=node_name,
            kind="graph",
            status="started" if stage is not None else "skipped",
            module=module,
        )
        return _update(
            state,
            transitions=(transition,),
            node_name=node_name,
            node_completed=True,
            active_module=module if stage is not None else None,
            status="running" if state.status == "ready" else _KEEP,
        )

    return enter


def _make_module_complete(module: ModuleName):
    node_name = f"{module}.complete"

    async def complete(raw_state: GraphState) -> dict[str, Any]:
        state = _state(raw_state)
        if _already_completed(state, node_name) or _is_halted(state):
            return {}
        transition = _transition(
            state,
            graph_name=module,
            node_name=node_name,
            kind="graph",
            status="completed",
            module=module,
            terminal_state="success",
        )
        return _update(
            state,
            transitions=(transition,),
            node_name=node_name,
            node_completed=True,
            completed_module=module,
            next_module=_next_module(state, module),
            active_module=None,
            module_outcome=ModuleOutcome(module=module, terminal_state="success"),
        )

    return complete


def _route_after_node(raw_state: GraphState) -> Literal["continue", "end"]:
    return "end" if _is_halted(_state(raw_state)) else "continue"


def _module_entry_route(
    raw_state: GraphState,
    module: ModuleName,
) -> Literal["run", "end"]:
    state = _state(raw_state)
    if _is_halted(state) or state.plan.stage_for(module) is None:
        return "end"
    return "run"


def compile_specialist_graph(
    module: ModuleName,
    invoker: ToolInvoker,
) -> CompiledStateGraph:
    """Compile one bounded, acyclic specialist graph."""

    builder = StateGraph(GraphState)
    entry_name = f"{module}.enter"
    complete_name = f"{module}.complete"
    builder.add_node(entry_name, _make_module_entry(module))
    builder.add_node(complete_name, _make_module_complete(module))

    tool_nodes: list[str] = []
    for tool_name in MODULE_TOOL_ORDER[module]:
        node_name = f"{module}.{tool_name}"
        tool_nodes.append(node_name)
        builder.add_node(
            node_name,
            _make_tool_node(
                graph_name=module,
                module=module,
                tool_name=tool_name,
                invoker=invoker,
            ),
        )

    first_node = tool_nodes[0] if tool_nodes else complete_name
    builder.add_edge(START, entry_name)
    builder.add_conditional_edges(
        entry_name,
        lambda state: _module_entry_route(state, module),
        {"run": first_node, "end": END},
    )
    for index, node_name in enumerate(tool_nodes):
        next_node = tool_nodes[index + 1] if index + 1 < len(tool_nodes) else complete_name
        builder.add_conditional_edges(
            node_name,
            _route_after_node,
            {"continue": next_node, "end": END},
        )
    builder.add_edge(complete_name, END)
    return builder.compile(name=f"carbonmesh.{module}.v1")


def compile_measurement_graph(invoker: ToolInvoker) -> CompiledStateGraph:
    return compile_specialist_graph("measurement", invoker)


def compile_assurance_graph(invoker: ToolInvoker) -> CompiledStateGraph:
    return compile_specialist_graph("assurance", invoker)


def compile_procurement_graph(invoker: ToolInvoker) -> CompiledStateGraph:
    return compile_specialist_graph("procurement", invoker)


def compile_dispatch_graph(invoker: ToolInvoker) -> CompiledStateGraph:
    return compile_specialist_graph("dispatch", invoker)


def _orchestrator_start(raw_state: GraphState) -> dict[str, Any]:
    state = _state(raw_state)
    node_name = "orchestrator.start"
    if _already_completed(state, node_name) or _is_halted(state):
        return {}
    transition = _transition(
        state,
        graph_name="orchestrator",
        node_name=node_name,
        kind="graph",
        status="started",
    )
    return _update(
        state,
        transitions=(transition,),
        node_name=node_name,
        node_completed=True,
        status="running",
    )


def _orchestrator_finalize(raw_state: GraphState) -> dict[str, Any]:
    state = _state(raw_state)
    node_name = "orchestrator.finalize"
    if _already_completed(state, node_name) or _is_halted(state):
        return {}
    transition = _transition(
        state,
        graph_name="orchestrator",
        node_name=node_name,
        kind="finalize",
        status="completed",
        terminal_state="success",
    )
    return _update(
        state,
        transitions=(transition,),
        node_name=node_name,
        node_completed=True,
        next_module=None,
        active_module=None,
        status="success",
        terminal_state="success",
        pending_interrupt=None,
        error_code=None,
    )


def compile_orchestrator_graph(
    invoker: ToolInvoker,
    *,
    measurement: CompiledStateGraph | None = None,
    assurance: CompiledStateGraph | None = None,
    procurement: CompiledStateGraph | None = None,
    dispatch: CompiledStateGraph | None = None,
) -> CompiledStateGraph:
    """Compile the canonical Measurement -> Assurance -> Procurement -> Dispatch DAG."""

    specialists = {
        "measurement": measurement or compile_measurement_graph(invoker),
        "assurance": assurance or compile_assurance_graph(invoker),
        "procurement": procurement or compile_procurement_graph(invoker),
        "dispatch": dispatch or compile_dispatch_graph(invoker),
    }
    builder = StateGraph(GraphState)
    builder.add_node("orchestrator.start", _orchestrator_start)

    context_nodes: list[str] = []
    for tool_name in CONTEXT_TOOL_ORDER:
        node_name = f"orchestrator.{tool_name}"
        context_nodes.append(node_name)
        builder.add_node(
            node_name,
            _make_tool_node(
                graph_name="orchestrator",
                module=None,
                tool_name=tool_name,
                invoker=invoker,
            ),
        )
    for module in MODULE_ORDER:
        builder.add_node(module, specialists[module])
    builder.add_node("orchestrator.finalize", _orchestrator_finalize)

    builder.add_edge(START, "orchestrator.start")
    builder.add_edge("orchestrator.start", context_nodes[0])
    sequence = context_nodes + list(MODULE_ORDER) + ["orchestrator.finalize"]
    for index, node_name in enumerate(sequence[:-1]):
        next_node = sequence[index + 1]
        builder.add_conditional_edges(
            node_name,
            _route_after_node,
            {"continue": next_node, "end": END},
        )
    builder.add_edge("orchestrator.finalize", END)
    return builder.compile(name="carbonmesh.orchestrator.v1")


def compile_agent_graphs(invoker: ToolInvoker) -> CompiledAgentGraphs:
    """Compile the complete five-graph runtime against one typed tool boundary."""

    measurement = compile_measurement_graph(invoker)
    assurance = compile_assurance_graph(invoker)
    procurement = compile_procurement_graph(invoker)
    dispatch = compile_dispatch_graph(invoker)
    orchestrator = compile_orchestrator_graph(
        invoker,
        measurement=measurement,
        assurance=assurance,
        procurement=procurement,
        dispatch=dispatch,
    )
    return CompiledAgentGraphs(
        orchestrator=orchestrator,
        measurement=measurement,
        assurance=assurance,
        procurement=procurement,
        dispatch=dispatch,
    )


__all__ = [
    "CompiledAgentGraphs",
    "compile_agent_graphs",
    "compile_assurance_graph",
    "compile_dispatch_graph",
    "compile_measurement_graph",
    "compile_orchestrator_graph",
    "compile_procurement_graph",
    "compile_specialist_graph",
]
