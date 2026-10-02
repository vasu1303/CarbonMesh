from __future__ import annotations

import asyncio
import json
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("langgraph")

from app.db.models.ai import AgentRun
from app.modules.agents.budget import new_budget
from app.modules.agents.context import freeze_runtime_context
from app.modules.agents.graph_contracts import (
    GraphState,
    GraphTransition,
    ToolOutput,
    ToolResult,
)
from app.modules.agents.llm.contracts import AIRequest, AIResult, AITokenUsage
from app.modules.agents.planning import PlannerSelection, build_execution_plan
from app.modules.agents.repository import (
    DurableToolInvocation,
    ResolvedContextReferences,
)
from app.modules.agents.runtime import GraphAgentRunService
from app.modules.agents.schemas import (
    AgentContextRequest,
    AgentQueryRequest,
    AgentTelemetry,
)
from app.modules.agents.step_recorder import (
    MAX_DURABLE_TOOL_RESULT_BYTES,
    AgentStepRecorder,
    DurableToolResultTooLargeError,
    durable_tool_result_snapshot,
)
from app.modules.agents.tasks import AgentTaskRegistry
from app.modules.agents.tools import (
    AgentToolContext,
    AgentToolFact,
    AgentToolName,
    AgentToolRegistry,
    AgentToolResult,
    BindClaimFactsInput,
    CalculateConfidenceInput,
    CalculateEmissionsInput,
    CreateApprovalPreviewInput,
    DecomposeClaimInput,
    DetectEvidenceGapsInput,
    MapStandardRequirementInput,
    NormalizeUnitInput,
    ResolveContextInput,
    RetrieveEvidenceInput,
    RetrieveLedgerFactsInput,
    SelectEmissionFactorInput,
    StrictToolInput,
    SyncGridHistoryInput,
    ValidateActivityInput,
    ValidateCitationsInput,
    WriteLedgerEventInput,
)


class ScriptedPlannerModel:
    provider = "gemini"
    model_id = "synthetic-durable-planner"

    def __init__(self, modules: tuple[str, ...]) -> None:
        self.modules = modules
        self.requests: list[AIRequest] = []

    async def generate(self, request: AIRequest) -> AIResult:
        self.requests.append(request)
        return AIResult(
            provider=self.provider,
            model_id=self.model_id,
            text=json.dumps(
                {
                    "disposition": "execute",
                    "modules": list(self.modules),
                    "clarification_fields": [],
                    "reason_code": "verified_workflow_requested",
                }
            ),
            usage=AITokenUsage(
                input_tokens=20,
                output_tokens=8,
                total_tokens=28,
                cached_input_tokens=3,
            ),
            provider_request_id="synthetic-durable-request",
            finish_reason="STOP",
            latency_ms=4,
        )


class DurableMemoryRepository:
    """Recorder-compatible store that snapshots every explicit commit."""

    def __init__(
        self,
        *,
        crash_after_committed_tool: AgentToolName | None = None,
    ) -> None:
        self.runs: dict[UUID, AgentRun] = {}
        self.steps: list[SimpleNamespace] = []
        self.commit_history: list[dict[str, object]] = []
        self.rollbacks = 0
        self.crash_after_committed_tool = crash_after_committed_tool

    async def resolve_context_references(self, **_: object) -> ResolvedContextReferences:
        return ResolvedContextReferences(
            actor_role="sustainability_analyst",
        )

    async def add(self, run: AgentRun) -> None:
        for field in (
            "model_calls",
            "tool_calls",
            "retry_count",
            "api_calls",
            "input_tokens",
            "output_tokens",
            "latency_ms",
        ):
            if getattr(run, field, None) is None:
                setattr(run, field, 0)
        self.runs[run.id] = run

    async def get(self, *, company_id: UUID, run_id: UUID) -> AgentRun | None:
        run = self.runs.get(run_id)
        return run if run is not None and run.company_id == company_id else None

    async def append_step(self, **values: Any) -> SimpleNamespace:
        step = SimpleNamespace(sequence=len(self.steps) + 1, **values)
        self.steps.append(step)
        return step

    async def reserve_tool_invocation(self, **values: Any) -> DurableToolInvocation:
        invocation_key = values["invocation_key"]
        matching = [
            step
            for step in reversed(self.steps)
            if step.graph_name == values["graph_name"]
            and step.node_name == values["node_name"]
            and step.tool_name == values["tool_name"]
            and step.input_snapshot.get("invocation_key") == invocation_key
        ]
        for step in matching:
            data = step.output_snapshot.get("data", {})
            if isinstance(data.get("durable_result"), dict):
                return DurableToolInvocation(
                    reserved=False,
                    result=ToolResult.model_validate_json(
                        json.dumps(data["durable_result"])
                    ),
                )
        if matching:
            return DurableToolInvocation(reserved=False)
        await self.append_step(
            company_id=values["company_id"],
            run_id=values["run_id"],
            step_type=values["step_type"],
            status="running",
            graph_name=values["graph_name"],
            node_name=values["node_name"],
            tool_name=values["tool_name"],
            input_snapshot=values["input_snapshot"],
            output_snapshot=values["output_snapshot"],
            started_at=values["started_at"],
        )
        return DurableToolInvocation(reserved=True)

    async def commit(self) -> None:
        run = next(iter(self.runs.values()), None)
        if run is None:
            return
        telemetry = dict(run.telemetry or {})
        checkpoint = telemetry.get("checkpoint")
        graph_state = telemetry.get("graph_state")
        self.commit_history.append(
            {
                "stage": run.stage,
                "terminal_state": run.terminal_state,
                "step_count": len(self.steps),
                "checkpoint_revision": (
                    checkpoint.get("revision") if isinstance(checkpoint, dict) else None
                ),
                "completed_nodes": (
                    list(checkpoint.get("completed_nodes", []))
                    if isinstance(checkpoint, dict)
                    else []
                ),
                "graph_state_persisted": isinstance(graph_state, dict),
            }
        )
        latest = self.steps[-1] if self.steps else None
        if (
            latest is not None
            and self.crash_after_committed_tool is not None
            and latest.tool_name == self.crash_after_committed_tool
            and latest.output_snapshot.get("event_name") == "tool.completed"
        ):
            self.crash_after_committed_tool = None
            raise SimulatedProcessCrashError

    async def rollback(self) -> None:
        self.rollbacks += 1

    def event_names(self) -> list[str]:
        return [step.output_snapshot["event_name"] for step in self.steps]


@dataclass(frozen=True, slots=True)
class RuntimeIds:
    company_id: UUID
    actor_id: UUID
    site_id: UUID
    reporting_period_id: UUID
    activity_record_id: UUID
    method_definition_id: UUID
    emission_factor_id: UUID
    measurement_id: UUID
    fact_id: UUID
    ledger_event_id: UUID
    standard_id: UUID
    disclosure_draft_id: UUID
    requirement_id: UUID
    claim_id: UUID
    evidence_item_id: UUID
    approval_id: UUID

    @classmethod
    def new(cls) -> RuntimeIds:
        return cls(*(uuid4() for _ in range(16)))


class SimulatedProcessCrashError(BaseException):
    """Models abrupt process loss beyond the runtime's safe Exception boundary."""


_EXPECTED_INPUT_TYPES: dict[str, type[StrictToolInput]] = {
    "resolve_context": ResolveContextInput,
    "validate_activity": ValidateActivityInput,
    "normalize_unit": NormalizeUnitInput,
    "sync_grid_history": SyncGridHistoryInput,
    "select_emission_factor": SelectEmissionFactorInput,
    "calculate_emissions": CalculateEmissionsInput,
    "calculate_confidence": CalculateConfidenceInput,
    "map_standard_requirement": MapStandardRequirementInput,
    "decompose_claim": DecomposeClaimInput,
    "retrieve_ledger_facts": RetrieveLedgerFactsInput,
    "retrieve_evidence": RetrieveEvidenceInput,
    "bind_claim_facts": BindClaimFactsInput,
    "validate_citations": ValidateCitationsInput,
    "detect_evidence_gaps": DetectEvidenceGapsInput,
    "create_approval_preview": CreateApprovalPreviewInput,
    "write_ledger_event": WriteLedgerEventInput,
}


class ChainedToolServicePort:
    def __init__(
        self,
        ids: RuntimeIds,
        *,
        crash_once_on: AgentToolName | None = None,
    ) -> None:
        self.ids = ids
        self.crash_once_on = crash_once_on
        self.calls: list[str] = []
        self.argument_types: list[type[StrictToolInput]] = []

    def handler_for(self, tool_name: AgentToolName):
        if tool_name not in _EXPECTED_INPUT_TYPES:
            return None

        async def handler(
            *,
            context: AgentToolContext,
            arguments: StrictToolInput,
        ) -> AgentToolResult:
            expected_type = _EXPECTED_INPUT_TYPES[tool_name]
            assert isinstance(arguments, expected_type)
            assert context.company_id == self.ids.company_id
            assert context.actor_id == self.ids.actor_id
            self.calls.append(tool_name)
            self.argument_types.append(type(arguments))
            if self.crash_once_on == tool_name:
                self.crash_once_on = None
                raise SimulatedProcessCrashError
            return self._result(tool_name, arguments)

        return handler

    def _result(
        self,
        tool_name: AgentToolName,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        ids = self.ids
        if tool_name == "resolve_context":
            assert isinstance(arguments, ResolveContextInput)
            assert arguments.include_metric_definitions is True
            return AgentToolResult(
                status="success",
                data={
                    "context_resolved": True,
                    "canonical_unit": "kg",
                    "period_start": "2026-07-01T00:00:00+00:00",
                    "period_end": "2026-10-01T00:00:00+00:00",
                },
            )
        if tool_name == "validate_activity":
            assert isinstance(arguments, ValidateActivityInput)
            assert arguments.activity_record_ids == (ids.activity_record_id,)
            return AgentToolResult(
                status="success",
                data={"activity_record_ids": [str(ids.activity_record_id)]},
                rows=1,
            )
        if tool_name == "normalize_unit":
            assert isinstance(arguments, NormalizeUnitInput)
            assert arguments.activity_record_ids == (ids.activity_record_id,)
            assert arguments.canonical_unit == "kg"
            return AgentToolResult(status="success", data={"units_normalized": True})
        if tool_name == "sync_grid_history":
            assert isinstance(arguments, SyncGridHistoryInput)
            return AgentToolResult(status="success", data={"grid_history_synced": True})
        if tool_name == "select_emission_factor":
            assert isinstance(arguments, SelectEmissionFactorInput)
            assert arguments.activity_record_ids == (ids.activity_record_id,)
            return AgentToolResult(
                status="success",
                data={
                    "emission_factor_id": str(ids.emission_factor_id),
                    "method_definition_id": str(ids.method_definition_id),
                },
            )
        if tool_name == "calculate_emissions":
            assert isinstance(arguments, CalculateEmissionsInput)
            assert arguments.emission_factor_id == ids.emission_factor_id
            assert arguments.method_definition_id == ids.method_definition_id
            return AgentToolResult(
                status="success",
                data={
                    "measurement_id": str(ids.measurement_id),
                    "ledger_event_type": "measurement.verified",
                    "ledger_subject_type": "carbon_measurement",
                    "ledger_subject_id": str(ids.measurement_id),
                    "source_event_ids": [],
                    "fact_ids": [str(ids.fact_id)],
                    "ledger_event_ids": [str(ids.ledger_event_id)],
                    "evidence_item_ids": [],
                    "payload_hash": "c" * 64,
                },
                facts=(
                    AgentToolFact(
                        fact_id=ids.fact_id,
                        metric_key="emissions.scope3.category1",
                        ledger_event_id=ids.ledger_event_id,
                        display_value="42.125 kgCO2e",
                    ),
                ),
                rows=1,
            )
        if tool_name == "calculate_confidence":
            assert isinstance(arguments, CalculateConfidenceInput)
            assert arguments.measurement_id == ids.measurement_id
            assert arguments.calculation_run_id is None
            assert arguments.method_definition_id == ids.method_definition_id
            return AgentToolResult(status="success", data={"confidence_calculated": True})
        if tool_name == "map_standard_requirement":
            assert isinstance(arguments, MapStandardRequirementInput)
            assert arguments.standard_id == ids.standard_id
            return AgentToolResult(
                status="success",
                data={
                    "requirement_id": str(ids.requirement_id),
                    "claim_id": str(ids.claim_id),
                    "claim_text": "Verified emissions disclosure claim.",
                    "approval_target_type": "disclosure_draft",
                    "approval_target_id": str(ids.disclosure_draft_id),
                    "payload_hash": "d" * 64,
                },
            )
        if tool_name == "retrieve_ledger_facts":
            assert isinstance(arguments, RetrieveLedgerFactsInput)
            return AgentToolResult(
                status="success",
                data={"ledger_event_ids": [str(ids.ledger_event_id)]},
                rows=1,
            )
        if tool_name == "retrieve_evidence":
            assert isinstance(arguments, RetrieveEvidenceInput)
            return AgentToolResult(
                status="success",
                data={"evidence_item_ids": [str(ids.evidence_item_id)]},
                chunks=1,
            )
        if tool_name == "bind_claim_facts":
            assert isinstance(arguments, BindClaimFactsInput)
            assert arguments.claim_id == ids.claim_id
            assert arguments.ledger_event_ids == (ids.ledger_event_id,)
            return AgentToolResult(
                status="success",
                data={"claim_ids": [str(ids.claim_id)]},
            )
        if tool_name == "validate_citations":
            assert isinstance(arguments, ValidateCitationsInput)
            assert arguments.claim_ids == (ids.claim_id,)
            return AgentToolResult(status="success", data={"citations_validated": True})
        if tool_name == "detect_evidence_gaps":
            assert isinstance(arguments, DetectEvidenceGapsInput)
            assert arguments.disclosure_draft_id == ids.disclosure_draft_id
            return AgentToolResult(
                status="success",
                data={
                    "approval_target_type": "disclosure_draft",
                    "approval_target_id": str(ids.disclosure_draft_id),
                    "payload_hash": "d" * 64,
                },
            )
        if tool_name == "create_approval_preview":
            assert isinstance(arguments, CreateApprovalPreviewInput)
            assert arguments.target_id == ids.disclosure_draft_id
            return AgentToolResult(
                status="approval_required",
                code="human_approval_required",
                data={
                    "approval_id": str(ids.approval_id),
                    "target_type": "disclosure_draft",
                    "target_id": str(ids.disclosure_draft_id),
                    "preview_hash": "d" * 64,
                    "analysis_signature": "e" * 64,
                    "expires_at": datetime(2099, 1, 1, tzinfo=UTC).isoformat(),
                },
            )
        if tool_name == "decompose_claim":
            assert isinstance(arguments, DecomposeClaimInput)
            assert arguments.disclosure_draft_id == ids.disclosure_draft_id
            assert arguments.requirement_id == ids.requirement_id
            return AgentToolResult(
                status="success",
                data={
                    "claim_id": str(ids.claim_id),
                    "claim_ids": [str(ids.claim_id)],
                    "claim_text": "Verified emissions disclosure claim.",
                },
            )
        if tool_name == "write_ledger_event":
            assert isinstance(arguments, WriteLedgerEventInput)
            assert arguments.subject_id == ids.measurement_id
            return AgentToolResult(
                status="success",
                data={"ledger_event_id": str(ids.ledger_event_id)},
            )
        raise AssertionError(f"unexpected typed tool handler: {tool_name}")


def _runtime_ids() -> RuntimeIds:
    return RuntimeIds.new()


def _request(ids: RuntimeIds) -> AgentQueryRequest:
    return AgentQueryRequest(
        query="Calculate verified emissions and prepare the requested disclosure workflow.",
        context=AgentContextRequest(
            company_id=ids.company_id,
            actor_id=ids.actor_id,
            site_id=ids.site_id,
            reporting_period_id=ids.reporting_period_id,
            activity_record_ids=[ids.activity_record_id],
            method_definition_id=ids.method_definition_id,
            standard_id=ids.standard_id,
            disclosure_draft_id=ids.disclosure_draft_id,
            requirement_ids=[ids.requirement_id],
            evidence_item_ids=[ids.evidence_item_id],
            metric_keys=["emissions.scope3.category1"],
        ),
    )


def _service(
    repository: DurableMemoryRepository,
    planner: ScriptedPlannerModel,
    port: ChainedToolServicePort,
) -> GraphAgentRunService:
    return GraphAgentRunService(
        repository,  # type: ignore[arg-type]
        model_factory=lambda: planner,
        tool_registry_factory=lambda: AgentToolRegistry(port),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_checkpoint_failure_drains_graph_children_before_recovery(
    monkeypatch: pytest.MonkeyPatch, failure: type[BaseException]
) -> None:
    ids = _runtime_ids()
    repository = DurableMemoryRepository()
    service = _service(
        repository, ScriptedPlannerModel(("measurement",)), ChainedToolServicePort(ids)
    )
    request = _request(ids)
    accepted = await service.start(request, trace_id="stream-cleanup")
    run = repository.runs[accepted.run_id]
    plan = build_execution_plan(
        PlannerSelection(
            disposition="execute",
            modules=("measurement",),
            reason_code="measurement_requested",
        )
    )
    _, context = freeze_runtime_context(
        request_context=request.context,
        actor_role="sustainability_analyst",
        query=request.query,
        plan=plan,
    )
    initial = GraphState.initial(run_id=run.id, context=context, plan=plan)
    emitted = initial.model_copy(
        update={
            "checkpoint": initial.checkpoint.model_copy(
                update={"last_transition_sequence": 1}
            ),
            "transitions": (
                GraphTransition(
                    sequence=1,
                    graph_name="orchestrator",
                    node_name="orchestrator.started",
                    kind="graph",
                    status="started",
                ),
            ),
        }
    )
    child_started = asyncio.Event()
    child_drained = asyncio.Event()

    async def child() -> None:
        try:
            child_started.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            child_drained.set()

    class StreamingGraph:
        async def astream(self, *_args, **_kwargs):
            task = asyncio.create_task(child())
            try:
                await child_started.wait()
                yield emitted
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def no_checkpoint(**_kwargs) -> None:
        pass

    async def fail_checkpoint(*_args, **_kwargs) -> None:
        assert child_started.is_set()
        raise failure("synthetic checkpoint interruption")

    monkeypatch.setattr(service, "_persist_graph_checkpoint", no_checkpoint)
    monkeypatch.setattr(service, "_persist_graph_transitions", fail_checkpoint)
    with pytest.raises(failure):
        await service._run_graph_durably(
            run=run,
            recorder=AgentStepRecorder(
                repository, company_id=ids.company_id, run_id=run.id  # type: ignore[arg-type]
            ),
            graph=StreamingGraph(),
            initial=initial,
            claim_id=uuid4(),
            runtime_budget=new_budget("single"),
            persistence_lock=asyncio.Lock(),
        )

    assert child_drained.is_set()


@pytest.mark.asyncio
async def test_graph_runtime_persists_incremental_checkpoints_and_exposes_typed_facts() -> None:
    ids = _runtime_ids()
    repository = DurableMemoryRepository()
    planner = ScriptedPlannerModel(("measurement",))
    port = ChainedToolServicePort(ids)
    service = _service(repository, planner, port)
    request = _request(ids)

    accepted = await service.start(request, trace_id="durable-success-trace")
    await service.execute(request=request, run_id=accepted.run_id)
    result = await service.get(company_id=ids.company_id, run_id=accepted.run_id)

    assert result.terminal_state == "success"
    assert result.stage == "completed"
    assert result.error_code is None
    assert len(result.facts) == 1
    assert result.facts[0].fact_id == ids.fact_id
    assert result.facts[0].ledger_event_id == ids.ledger_event_id
    assert result.facts[0].display_value == "42.125 kgCO2e"
    assert port.calls == [
        "resolve_context",
        "validate_activity",
        "normalize_unit",
        "select_emission_factor",
        "calculate_emissions",
        "calculate_confidence",
    ]
    assert len(planner.requests) == 1

    telemetry = AgentTelemetry.model_validate(result.telemetry)
    assert telemetry.graph_state is not None
    assert telemetry.checkpoint is not None
    assert telemetry.checkpoint["completed_modules"] == ["measurement"]
    assert telemetry.tool_calls == 6

    checkpoint_commits = [
        commit
        for commit in repository.commit_history
        if commit["checkpoint_revision"] is not None
    ]
    revisions = [int(commit["checkpoint_revision"]) for commit in checkpoint_commits]
    assert revisions == sorted(revisions)
    assert len(set(revisions)) >= 4
    assert any(
        commit["stage"] == "graph"
        and commit["terminal_state"] == "running"
        and commit["graph_state_persisted"]
        for commit in checkpoint_commits
    )

    completed_tools = [
        step.tool_name
        for step in repository.steps
        if step.output_snapshot["event_name"] == "tool.completed"
    ]
    assert completed_tools == port.calls
    assert {"node", "tool", "finalize"}.issubset(
        {step.step_type for step in repository.steps}
    )
    assert repository.event_names().count("fact.created") == 1
    assert "node.completed" in repository.event_names()
    assert "run.completed" in repository.event_names()


@pytest.mark.asyncio
async def test_planned_crash_recovery_preserves_exhausted_planning_latency() -> None:
    ids = _runtime_ids()
    repository = DurableMemoryRepository()
    planner = ScriptedPlannerModel(("measurement", "assurance"))
    port = ChainedToolServicePort(ids)
    service = _service(repository, planner, port)
    request = _request(ids)
    accepted = await service.start(request, trace_id="planned-latency-recovery")
    run = repository.runs[accepted.run_id]
    plan = build_execution_plan(
        PlannerSelection(
            disposition="execute",
            modules=("measurement", "assurance"),
            reason_code="cross_module_request",
        )
    )
    api_context, graph_context = freeze_runtime_context(
        request_context=request.context,
        actor_role="sustainability_analyst",
        query=request.query,
        plan=plan,
    )
    run.stage = "planned"
    run.terminal_state = "running"
    run.plan = plan.model_dump(mode="json")
    run.context_envelope = api_context.model_dump(mode="json")
    run.context_hash = graph_context.analysis_signature
    run.latency_ms = 45_000
    run.telemetry = AgentTelemetry.model_validate(run.telemetry).model_copy(
        update={
            "analysis_signature": graph_context.analysis_signature,
            "graph_context": graph_context.model_dump(mode="json"),
            "elapsed_ms": 45_000,
        }
    ).model_dump(mode="json")

    await service.execute_resumed(
        company_id=ids.company_id,
        run_id=accepted.run_id,
    )
    result = await service.get(company_id=ids.company_id, run_id=accepted.run_id)

    assert result.terminal_state == "budget_exhausted"
    assert result.error_code == "latency_budget_exhausted"
    assert port.calls == []
    assert result.telemetry.elapsed_ms >= 45_000


@pytest.mark.asyncio
async def test_crash_after_mutating_tool_fails_closed_without_replay() -> None:
    ids = _runtime_ids()
    repository = DurableMemoryRepository()
    planner = ScriptedPlannerModel(("measurement", "assurance"))
    port = ChainedToolServicePort(ids, crash_once_on="map_standard_requirement")
    service = _service(repository, planner, port)
    request = _request(ids)

    accepted = await service.start(request, trace_id="durable-reentry-trace")
    with pytest.raises(SimulatedProcessCrashError):
        await service.execute(request=request, run_id=accepted.run_id)

    interrupted_run = repository.runs[accepted.run_id]
    interrupted_telemetry = AgentTelemetry.model_validate(interrupted_run.telemetry)
    assert interrupted_run.stage == "graph"
    assert interrupted_run.terminal_state == "running"
    assert interrupted_telemetry.graph_state is not None
    assert interrupted_telemetry.checkpoint is not None
    assert interrupted_telemetry.checkpoint["completed_modules"] == ["measurement"]
    assert Counter(port.calls) == Counter(
        {
            "resolve_context": 1,
            "validate_activity": 1,
            "select_emission_factor": 1,
            "calculate_emissions": 1,
            "calculate_confidence": 1,
            "map_standard_requirement": 1,
        }
    )

    await service.execute_resumed(company_id=ids.company_id, run_id=accepted.run_id)
    result = await service.get(company_id=ids.company_id, run_id=accepted.run_id)

    counts = Counter(port.calls)
    assert result.facts[0].fact_id == ids.fact_id
    assert counts["resolve_context"] == 1
    assert counts["validate_activity"] == 1
    assert counts["select_emission_factor"] == 1
    assert counts["calculate_emissions"] == 1
    assert counts["calculate_confidence"] == 1
    assert counts["map_standard_requirement"] == 1
    assert counts["create_approval_preview"] == 0
    assert len(planner.requests) == 1
    assert repository.event_names().count("tool.started") == len(port.calls)
    assert result.terminal_state == "stale"
    assert result.error_code == "tool_outcome_indeterminate"
    assert repository.event_names().count("approval.required") == 0
    assert repository.event_names().count("run.stopped") == 1


@pytest.mark.asyncio
async def test_crash_after_durable_specialist_tool_continues_without_reexecution() -> None:
    ids = _runtime_ids()
    repository = DurableMemoryRepository(
        crash_after_committed_tool="retrieve_evidence"
    )
    planner = ScriptedPlannerModel(("measurement", "assurance"))
    port = ChainedToolServicePort(ids)
    service = _service(repository, planner, port)
    request = _request(ids)

    accepted = await service.start(request, trace_id="durable-specialist-replay-trace")
    with pytest.raises(SimulatedProcessCrashError):
        await service.execute(request=request, run_id=accepted.run_id)

    checkpointed = AgentTelemetry.model_validate(
        repository.runs[accepted.run_id].telemetry
    )
    assert checkpointed.checkpoint is not None
    assert checkpointed.checkpoint["completed_modules"] == ["measurement"]
    assert "assurance.map_standard_requirement" in checkpointed.checkpoint[
        "completed_nodes"
    ]
    assert "assurance.decompose_claim" in checkpointed.checkpoint["completed_nodes"]
    assert "assurance.retrieve_evidence" not in checkpointed.checkpoint[
        "completed_nodes"
    ]
    assert checkpointed.graph_state is not None
    assert checkpointed.graph_state["budget"]["tool_calls"] >= 7
    assert Counter(port.calls)["map_standard_requirement"] == 1
    assert Counter(port.calls)["decompose_claim"] == 1
    assert Counter(port.calls)["retrieve_evidence"] == 1

    await service.execute_resumed(
        company_id=ids.company_id,
        run_id=accepted.run_id,
    )
    result = await service.get(company_id=ids.company_id, run_id=accepted.run_id)

    counts = Counter(port.calls)
    assert counts["map_standard_requirement"] == 1
    assert counts["decompose_claim"] == 1
    assert counts["retrieve_ledger_facts"] == 0
    assert counts["retrieve_evidence"] == 1
    assert counts["bind_claim_facts"] == 1
    assert counts["validate_citations"] == 1
    assert counts["detect_evidence_gaps"] == 0
    assert counts["create_approval_preview"] == 1
    assert result.terminal_state == "approval_required"
    assert result.error_code == "human_approval_required"
    assert repository.event_names().count("tool.started") == len(port.calls)
    assert sum(
        step.tool_name == "retrieve_evidence"
        and step.output_snapshot.get("event_name") == "tool.completed"
        for step in repository.steps
    ) == 1


def test_durable_tool_result_snapshot_enforces_byte_cap() -> None:
    oversized = ToolResult(
        outputs=(
            ToolOutput(
                key="oversized_value",
                value="x" * MAX_DURABLE_TOOL_RESULT_BYTES,
            ),
        )
    )

    with pytest.raises(DurableToolResultTooLargeError):
        durable_tool_result_snapshot(oversized)


@pytest.mark.asyncio
async def test_long_lived_recovery_task_is_cancelled_without_shutdown_grace_delay() -> None:
    registry = AgentTaskRegistry()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def recovery_worker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    registry.spawn(
        recovery_worker(),
        name="periodic-agent-recovery",
        cancel_on_shutdown=True,
    )
    await started.wait()

    await asyncio.wait_for(registry.shutdown(timeout_seconds=20), timeout=0.5)

    assert cancelled.is_set()
