from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import app.modules.agents.budget as agent_budget
import app.modules.agents.runtime as agent_runtime
from app.db.models.ai import AgentRun
from app.modules.agents.budget import RuntimeBudgetCounter, RuntimeBudgetExhausted
from app.modules.agents.graph_contracts import (
    ApprovalInterrupt,
    BudgetLimits,
    BudgetState,
    ContextEnvelope,
    ExecutionPlan,
    GraphState,
    GraphTransition,
    PlanStage,
)
from app.modules.agents.llm.contracts import AIResult, AITokenUsage
from app.modules.agents.repository import ResolvedContextReferences
from app.modules.agents.resume import ApprovalResumeState
from app.modules.agents.runtime import (
    AgentResumeError,
    GraphAgentRunService,
    _resume_execution_budget,
    _terminal_elapsed_ms,
)
from app.modules.agents.schemas import (
    AgentContextRequest,
    AgentResumeRequest,
    AgentRunPayload,
    AgentTelemetry,
    ApprovalRequirement,
    FrozenContextEnvelope,
    PendingInterrupt,
)

REQUEST_HASH = "1" * 64
INTERRUPT_SIGNATURE = "2" * 64
PREVIEW_HASH = "3" * 64
APPROVAL_SIGNATURE = "4" * 64
APPROVAL_CONTEXT_HASH = "5" * 64


class InMemoryResumeRepository:
    """Small recorder-compatible repository for the durable resume boundary."""

    def __init__(self, run: AgentRun, interrupt_sequence: int) -> None:
        self.run = run
        self.steps = [
            SimpleNamespace(
                sequence=interrupt_sequence,
                step_type="interrupt",
                output_snapshot={
                    "event_name": (
                        "approval.required"
                        if run.terminal_state == "approval_required"
                        else "clarification.required"
                    ),
                    "data": {},
                },
            )
        ]
        self.commits = 0
        self.rollbacks = 0
        self.actor_role_override: str | None = None

    async def get_for_update(self, *, company_id: UUID, run_id: UUID):
        if self.run.company_id == company_id and self.run.id == run_id:
            return self.run
        return None

    async def get(self, *, company_id: UUID, run_id: UUID):
        return await self.get_for_update(company_id=company_id, run_id=run_id)

    async def resolve_context_references(self, **_):
        return ResolvedContextReferences(
            actor_role=self.actor_role_override
            or (
                "approver"
                if self.run.terminal_state == "approval_required"
                else "sustainability_analyst"
            )
        )

    async def latest_interrupt(self, *, company_id: UUID, run_id: UUID):
        matching = [
            step
            for step in self.steps
            if step.step_type == "interrupt"
            and self.run.company_id == company_id
            and self.run.id == run_id
        ]
        return max(matching, key=lambda step: step.sequence, default=None)

    async def list_steps_after(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        after_sequence: int = 0,
        limit: int = 100,
    ):
        if self.run.company_id != company_id or self.run.id != run_id:
            return []
        return [
            step for step in self.steps if step.sequence > after_sequence
        ][:limit]

    async def append_step(self, **values):
        sequence = max((step.sequence for step in self.steps), default=0) + 1
        step = SimpleNamespace(sequence=sequence, **values)
        self.steps.append(step)
        return step

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def resumed_steps(self) -> list[SimpleNamespace]:
        return [
            step
            for step in self.steps
            if step.output_snapshot.get("event_name") == "run.resumed"
        ]


class ObservedApproval:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls: list[dict[str, object]] = []
        self.decision_commits = 0

    async def validate(self, **values) -> ApprovalResumeState:
        self.calls.append(values)
        return ApprovalResumeState(self.status, f"approval_{self.status}")  # type: ignore[arg-type]

    async def commit_decision(self, **_) -> None:
        self.decision_commits += 1
        raise AssertionError("the agent runtime must never commit approval decisions")


class ClarifiedIntentModel:
    provider = "gemini"
    model_id = "clarification-replan-test"

    async def generate(self, _request) -> AIResult:
        return AIResult(
            provider=self.provider,
            model_id=self.model_id,
            text=(
                '{"disposition":"execute","modules":["measurement"],'
                '"clarification_fields":[],"reason_code":"measurement_request"}'
            ),
            usage=AITokenUsage(
                input_tokens=8,
                output_tokens=4,
                total_tokens=12,
            ),
            provider_request_id="clarification-replan-1",
            finish_reason="STOP",
            latency_ms=2,
        )


def _plan(module: str = "measurement") -> ExecutionPlan:
    if module == "procurement":
        stage = PlanStage(
            stage_id="procurement.resume",
            module="procurement",
            tool_names=("create_approval_preview",),
            requires_human_approval=True,
        )
    else:
        stage = PlanStage(stage_id="measurement.resume", module="measurement")
    return ExecutionPlan(profile="single", stages=(stage,))


def _golden_resume_state(*, elapsed_ms: int) -> GraphState:
    plan = ExecutionPlan(
        profile="golden",
        stages=(
            PlanStage(stage_id="measurement.run", module="measurement"),
            PlanStage(
                stage_id="assurance.run",
                module="assurance",
                depends_on=("measurement",),
            ),
        ),
    )
    context = ContextEnvelope(
        company_id=uuid4(),
        actor_id=uuid4(),
        actor_role="sustainability_analyst",
        modules=plan.modules,
        metric_keys=("emissions.scope2.location_based",),
        request_hash=REQUEST_HASH,
        analysis_signature=INTERRUPT_SIGNATURE,
    )
    return GraphState.initial(
        run_id=uuid4(),
        context=context,
        plan=plan,
        budget=BudgetState.for_profile("golden").model_copy(
            update={"elapsed_ms": elapsed_ms}
        ),
    )


def test_sequential_approval_resumes_share_original_deadline_and_ten_second_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0

    def fake_clock() -> float:
        return now

    monkeypatch.setattr(agent_budget, "monotonic", fake_clock)

    for elapsed_ms, expected_remaining in (
        (5_000, 10.0),
        (18_000, 10.0),
        (44_000, 1.0),
    ):
        budget = _resume_execution_budget(
            state=_golden_resume_state(elapsed_ms=elapsed_ms),
            persisted_elapsed_ms=elapsed_ms,
            model_calls=1,
            repairs=0,
            approval_resume=True,
            resume_segment_elapsed_ms=0,
        )

        assert budget.limits.max_tool_calls == 20
        assert budget.remaining_seconds == pytest.approx(expected_remaining)

    now += 1.0
    with pytest.raises(RuntimeBudgetExhausted, match="latency budget exhausted"):
        budget.check_deadline()


def test_approval_validation_consumes_the_same_ten_second_resume_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0

    def fake_clock() -> float:
        return now

    monkeypatch.setattr(agent_budget, "monotonic", fake_clock)
    budget = _resume_execution_budget(
        state=_golden_resume_state(elapsed_ms=8_000),
        persisted_elapsed_ms=8_000,
        model_calls=1,
        repairs=0,
        approval_resume=True,
        resume_segment_elapsed_ms=3_000,
    )

    assert budget.remaining_seconds == pytest.approx(7.0)


def test_terminal_latency_does_not_count_persisted_planning_time_twice() -> None:
    assert _terminal_elapsed_ms(
        persisted_elapsed_ms=5_000,
        segment_elapsed_ms=7_000,
        budget_elapsed_ms=7_000,
        graph_elapsed_ms=7_000,
        has_graph_state=True,
    ) == 7_000


def _api_context(*, company_id: UUID, actor_id: UUID) -> FrozenContextEnvelope:
    return FrozenContextEnvelope(
        company_id=company_id,
        site_id=None,
        reporting_period_id=None,
        workflow="measurement",
        metric_keys=["emissions.scope2.location_based"],
        material_scope=[],
        supplier_product_ids=[],
        actor_id=actor_id,
        actor_role="sustainability_analyst",
        constraints={},
        request_hash=REQUEST_HASH,
        analysis_signature=INTERRUPT_SIGNATURE,
    )


def _run(
    *,
    pending: PendingInterrupt,
    plan: ExecutionPlan,
    graph_state: GraphState | None = None,
) -> AgentRun:
    company_id = uuid4()
    actor_id = uuid4()
    context = _api_context(company_id=company_id, actor_id=actor_id)
    telemetry = AgentTelemetry(
        trace_id="resume-trace",
        analysis_signature=INTERRUPT_SIGNATURE,
        elapsed_ms=0,
        graph_context=(
            graph_state.context.model_dump(mode="json") if graph_state is not None else None
        ),
        graph_state=(graph_state.model_dump(mode="json") if graph_state is not None else None),
        checkpoint=(
            graph_state.checkpoint.model_dump(mode="json")
            if graph_state is not None
            else None
        ),
    )
    return AgentRun(
        id=graph_state.run_id if graph_state is not None else uuid4(),
        company_id=company_id,
        actor_id=actor_id,
        trace_id="resume-trace",
        workflow=plan.modules[0],
        stage="interrupted",
        terminal_state=(
            "approval_required" if pending.kind == "approval" else "needs_clarification"
        ),
        context_envelope=context.model_dump(mode="json"),
        context_hash=INTERRUPT_SIGNATURE,
        plan=plan.model_dump(mode="json"),
        result=AgentRunPayload(
            message="The run is waiting for a durable interrupt.",
            approval_requirement=(
                ApprovalRequirement(
                    required=True,
                    approval_id=pending.approval_id,
                    recommendation_id=uuid4(),
                    preview_hash=pending.preview_hash,
                )
                if pending.kind == "approval"
                else ApprovalRequirement()
            ),
            missing_fields=pending.missing_fields,
            pending_interrupt=pending,
        ).model_dump(mode="json"),
        telemetry=telemetry.model_dump(mode="json"),
        model_calls=0,
        tool_calls=0,
        retry_count=0,
        api_calls=0,
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        error_code="interrupted",
    )


def _clarification_fixture(
    *, interrupt_sequence: int = 4
) -> tuple[AgentRun, AgentResumeRequest]:
    interrupt_id = uuid4()
    plan = _plan()
    pending = PendingInterrupt(
        interrupt_id=interrupt_id,
        sequence=interrupt_sequence,
        kind="clarification",
        analysis_signature=INTERRUPT_SIGNATURE,
        missing_fields=["context.site_id", "context.reporting_period_id"],
    )
    run = _run(pending=pending, plan=plan)
    request = AgentResumeRequest(
        company_id=run.company_id,
        actor_id=run.actor_id,
        interrupt_id=interrupt_id,
        interrupt_sequence=interrupt_sequence,
        analysis_signature=INTERRUPT_SIGNATURE,
        idempotency_key="clarification-resume-key",
        clarification={
            "context": AgentContextRequest(
                company_id=run.company_id,
                actor_id=run.actor_id,
                site_id=uuid4(),
                reporting_period_id=uuid4(),
                activity_record_ids=[uuid4()],
            )
        },
    )
    return run, request


def _approval_fixture(
    *,
    interrupt_sequence: int = 7,
    approval_context_hash: str | None = None,
) -> tuple[AgentRun, AgentResumeRequest, str]:
    company_id = uuid4()
    actor_id = uuid4()
    run_id = uuid4()
    plan = _plan("procurement")
    graph_context = ContextEnvelope(
        company_id=company_id,
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        actor_id=actor_id,
        actor_role="sustainability_analyst",
        modules=("procurement",),
        metric_keys=("emissions.scope3.category1",),
        request_hash=REQUEST_HASH,
        analysis_signature=INTERRUPT_SIGNATURE,
    )
    approval_id = uuid4()
    target_id = uuid4()
    approval_interrupt = ApprovalInterrupt(
        approval_id=approval_id,
        target_type="procurement_recommendation",
        target_id=target_id,
        preview_hash=PREVIEW_HASH,
        analysis_signature=APPROVAL_SIGNATURE,
        context_hash=INTERRUPT_SIGNATURE,
        approval_context_hash=approval_context_hash,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    node_name = "procurement.create_approval_preview"
    transition = GraphTransition(
        sequence=1,
        graph_name="procurement",
        node_name=node_name,
        kind="interrupt",
        status="interrupted",
        module="procurement",
        tool_name="create_approval_preview",
        terminal_state="approval_required",
        code="human_approval_required",
    )
    initial = GraphState.initial(
        run_id=run_id,
        context=graph_context,
        plan=plan,
        budget=BudgetState.for_profile("single"),
    )
    checkpoint = initial.checkpoint.model_copy(
        update={
            "last_transition_sequence": 1,
            "pending_interrupt": approval_interrupt,
        }
    )
    graph_state = GraphState.model_validate(
        initial.model_copy(
            update={
                "checkpoint": checkpoint,
                "status": "interrupted",
                "terminal_state": "approval_required",
                "active_module": "procurement",
                "transitions": (transition,),
                "pending_interrupt": approval_interrupt,
                "error_code": "human_approval_required",
            }
        ).model_dump(mode="python")
    )
    pending = PendingInterrupt(
        interrupt_id=uuid4(),
        sequence=interrupt_sequence,
        kind="approval",
        analysis_signature=APPROVAL_SIGNATURE,
        context_hash=INTERRUPT_SIGNATURE,
        approval_context_hash=approval_context_hash,
        approval_id=approval_id,
        target_type="procurement_recommendation",
        target_id=target_id,
        preview_hash=PREVIEW_HASH,
        expires_at=approval_interrupt.expires_at,
    )
    run = _run(pending=pending, plan=plan, graph_state=graph_state)
    run.company_id = company_id
    run.actor_id = actor_id
    run.context_envelope = _api_context(
        company_id=company_id,
        actor_id=actor_id,
    ).model_dump(mode="json")
    request = AgentResumeRequest(
        company_id=company_id,
        actor_id=actor_id,
        interrupt_id=pending.interrupt_id,
        interrupt_sequence=interrupt_sequence,
        analysis_signature=APPROVAL_SIGNATURE,
        idempotency_key="approval-resume-key",
        approval={"approval_id": approval_id, "preview_hash": PREVIEW_HASH},
    )
    return run, request, node_name


@pytest.mark.asyncio
async def test_clarification_resume_reconstructs_and_commits_running_checkpoint() -> None:
    run, request = _clarification_fixture()
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    service = GraphAgentRunService(repository)  # type: ignore[arg-type]

    result = await service.resume(run_id=run.id, request=request)

    assert result.resumed is True
    assert result.terminal_state == "running"
    assert run.terminal_state == "running"
    assert run.stage == "resumed_clarification"
    assert run.completed_at is None
    assert run.error_code is None
    assert repository.commits == 1
    assert len(repository.resumed_steps()) == 1
    telemetry = AgentTelemetry.model_validate(run.telemetry)
    assert telemetry.graph_state is not None
    checkpoint = telemetry.graph_state["checkpoint"]
    assert checkpoint == telemetry.checkpoint
    assert checkpoint["pending_interrupt"] is None
    assert run.context_hash == telemetry.analysis_signature
    assert run.context_hash != INTERRUPT_SIGNATURE
    assert AgentRunPayload.model_validate(run.result).pending_interrupt is None


@pytest.mark.asyncio
async def test_planner_clarification_replans_without_resetting_or_double_counting_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0

    def fake_clock() -> float:
        return now

    class TimedClarifiedIntentModel(ClarifiedIntentModel):
        async def generate(self, request) -> AIResult:
            nonlocal now
            now += 3.0
            return await super().generate(request)

    monkeypatch.setattr(agent_budget, "monotonic", fake_clock)
    interrupt_id = uuid4()
    pending = PendingInterrupt(
        interrupt_id=interrupt_id,
        sequence=4,
        kind="clarification",
        analysis_signature=INTERRUPT_SIGNATURE,
        missing_fields=["query.intent"],
    )
    run = _run(pending=pending, plan=_plan())
    run.plan = None
    run.workflow = "planning"
    run.model_calls = 1
    run.latency_ms = 5_000
    run.telemetry = AgentTelemetry.model_validate(run.telemetry).model_copy(
        update={"model_calls": 1, "elapsed_ms": 5_000}
    ).model_dump(mode="json")
    clarified_query = "Measure the verified emissions for this activity."
    request = AgentResumeRequest(
        company_id=run.company_id,
        actor_id=run.actor_id,
        interrupt_id=interrupt_id,
        interrupt_sequence=4,
        analysis_signature=INTERRUPT_SIGNATURE,
        idempotency_key="planner-clarification-resume",
        clarification={
            "context": AgentContextRequest(
                company_id=run.company_id,
                actor_id=run.actor_id,
                site_id=uuid4(),
                reporting_period_id=uuid4(),
                activity_record_ids=[uuid4()],
            ),
            "clarified_query": clarified_query,
        },
    )
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    service = GraphAgentRunService(
        repository,  # type: ignore[arg-type]
        model_factory=TimedClarifiedIntentModel,
    )

    result = await service.resume(run_id=run.id, request=request)

    assert result.resumed is True
    assert run.terminal_state == "running"
    assert run.stage == "resumed_clarification"
    assert run.workflow == "measurement"
    assert run.plan is not None
    assert run.model_calls == 2
    frozen = FrozenContextEnvelope.model_validate(run.context_envelope)
    assert frozen.request_hash == sha256(clarified_query.encode()).hexdigest()
    assert clarified_query not in str(run.context_envelope)
    assert clarified_query not in str(run.telemetry)
    assert clarified_query not in str(repository.steps)
    telemetry = AgentTelemetry.model_validate(run.telemetry)
    assert telemetry.graph_state is not None
    graph_state = GraphState.model_validate_json(json.dumps(telemetry.graph_state))
    assert graph_state.plan.modules == ("measurement",)
    assert graph_state.budget.model_calls == 2
    assert run.latency_ms == 8_000
    assert telemetry.elapsed_ms == 8_000
    assert graph_state.budget.elapsed_ms == 8_000


@pytest.mark.asyncio
async def test_planner_clarification_model_uses_time_remaining_from_resume_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0

    def fake_clock() -> float:
        return now

    class SlowEntryRepository(InMemoryResumeRepository):
        async def get_for_update(self, *, company_id: UUID, run_id: UUID):
            nonlocal now
            observed = await super().get_for_update(
                company_id=company_id,
                run_id=run_id,
            )
            now += 9.0
            return observed

    class TwoSecondClarifiedIntentModel(ClarifiedIntentModel):
        calls = 0

        async def generate(self, request) -> AIResult:
            nonlocal now
            type(self).calls += 1
            now += 2.0
            return await super().generate(request)

    monkeypatch.setattr(agent_budget, "monotonic", fake_clock)
    interrupt_id = uuid4()
    pending = PendingInterrupt(
        interrupt_id=interrupt_id,
        sequence=4,
        kind="clarification",
        analysis_signature=INTERRUPT_SIGNATURE,
        missing_fields=["query.intent"],
    )
    run = _run(pending=pending, plan=_plan())
    run.plan = None
    request = AgentResumeRequest(
        company_id=run.company_id,
        actor_id=run.actor_id,
        interrupt_id=interrupt_id,
        interrupt_sequence=4,
        analysis_signature=INTERRUPT_SIGNATURE,
        idempotency_key="slow-planner-clarification-resume",
        clarification={
            "context": AgentContextRequest(
                company_id=run.company_id,
                actor_id=run.actor_id,
                site_id=uuid4(),
                reporting_period_id=uuid4(),
                activity_record_ids=[uuid4()],
            ),
            "clarified_query": "Measure verified emissions for this activity.",
        },
    )
    repository = SlowEntryRepository(run, request.interrupt_sequence)
    service = GraphAgentRunService(
        repository,  # type: ignore[arg-type]
        model_factory=TwoSecondClarifiedIntentModel,
    )

    with pytest.raises(AgentResumeError) as caught:
        await service.resume(run_id=run.id, request=request)

    assert caught.value.code == "latency_budget_exhausted"
    assert TwoSecondClarifiedIntentModel.calls == 1
    assert run.terminal_state == "needs_clarification"
    assert run.plan is None


@pytest.mark.asyncio
async def test_planner_clarification_requires_new_query_when_plan_is_absent() -> None:
    run, request = _clarification_fixture()
    run.plan = None
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    service = GraphAgentRunService(repository)  # type: ignore[arg-type]

    with pytest.raises(AgentResumeError) as caught:
        await service.resume(run_id=run.id, request=request)

    assert caught.value.code == "clarified_query_required"
    assert run.terminal_state == "needs_clarification"


@pytest.mark.asyncio
async def test_approval_resume_fails_closed_without_person2_observer() -> None:
    run, request, _ = _approval_fixture()
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    service = GraphAgentRunService(repository)  # type: ignore[arg-type]

    with pytest.raises(AgentResumeError) as caught:
        await service.resume(run_id=run.id, request=request)

    assert caught.value.code == "approval_resume_unavailable"
    assert caught.value.status_code == 503
    assert run.terminal_state == "approval_required"
    assert repository.resumed_steps() == []


@pytest.mark.asyncio
async def test_pending_approval_is_observed_without_resuming_or_mutating_run() -> None:
    run, request, _ = _approval_fixture()
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    approval = ObservedApproval("pending")
    service = GraphAgentRunService(
        repository,  # type: ignore[arg-type]
        approval_resume=approval,  # type: ignore[arg-type]
    )

    result = await service.resume(run_id=run.id, request=request)

    assert result.resumed is False
    assert result.terminal_state == "approval_required"
    assert run.terminal_state == "approval_required"
    assert run.stage == "interrupted"
    assert repository.commits == 0
    assert repository.resumed_steps() == []
    assert len(approval.calls) == 1
    assert approval.calls[0]["analysis_signature"] == APPROVAL_SIGNATURE
    assert approval.calls[0]["context_hash"] == INTERRUPT_SIGNATURE
    assert approval.decision_commits == 0


@pytest.mark.asyncio
async def test_approval_validation_is_cancelled_within_the_resume_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = asyncio.Event()

    class SlowApproval(ObservedApproval):
        async def validate(self, **values) -> ApprovalResumeState:
            self.calls.append(values)
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()
            raise AssertionError("cancelled approval validation must not return")

    original_new_budget = agent_runtime.new_budget

    def short_resume_budget(profile):
        if profile == "resume":
            return RuntimeBudgetCounter(
                BudgetLimits(
                    max_model_calls=1,
                    max_tool_calls=3,
                    max_repairs_per_stage=0,
                    target_latency_ms=20,
                )
            )
        return original_new_budget(profile)

    monkeypatch.setattr(agent_runtime, "new_budget", short_resume_budget)
    run, request, _ = _approval_fixture()
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    approval = SlowApproval("approved")
    service = GraphAgentRunService(
        repository,  # type: ignore[arg-type]
        approval_resume=approval,  # type: ignore[arg-type]
    )

    with pytest.raises(AgentResumeError) as caught:
        await service.resume(run_id=run.id, request=request)

    assert caught.value.code == "latency_budget_exhausted"
    assert cancelled.is_set()
    assert run.terminal_state == "approval_required"
    assert repository.commits == 0
    assert repository.resumed_steps() == []


@pytest.mark.asyncio
async def test_approved_resume_marks_interrupted_tool_complete_without_deciding() -> None:
    run, request, interrupted_node = _approval_fixture()
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    approval = ObservedApproval("approved")
    service = GraphAgentRunService(
        repository,  # type: ignore[arg-type]
        approval_resume=approval,  # type: ignore[arg-type]
    )

    result = await service.resume(run_id=run.id, request=request)

    assert result.resumed is True
    assert result.terminal_state == "running"
    assert repository.commits == 1
    assert len(repository.resumed_steps()) == 1
    persisted_state = AgentTelemetry.model_validate(run.telemetry).graph_state
    resumed = GraphState.model_validate_json(json.dumps(persisted_state))
    assert resumed.status == "running"
    assert resumed.pending_interrupt is None
    assert interrupted_node in resumed.checkpoint.completed_nodes
    assert resumed.checkpoint.revision == 1
    assert len(approval.calls) == 1
    assert approval.calls[0]["analysis_signature"] == APPROVAL_SIGNATURE
    assert approval.calls[0]["context_hash"] == INTERRUPT_SIGNATURE
    assert approval.decision_commits == 0


@pytest.mark.asyncio
async def test_accepted_approval_persists_resume_elapsed_in_the_graph_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0

    def fake_clock() -> float:
        return now

    class TimedApproval(ObservedApproval):
        async def validate(self, **values) -> ApprovalResumeState:
            nonlocal now
            self.calls.append(values)
            now += 3.0
            return ApprovalResumeState("approved", "approval_approved")

    monkeypatch.setattr(agent_budget, "monotonic", fake_clock)
    run, request, _ = _approval_fixture()
    run.latency_ms = 5_000
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    approval = TimedApproval("approved")
    service = GraphAgentRunService(
        repository,  # type: ignore[arg-type]
        approval_resume=approval,  # type: ignore[arg-type]
    )

    result = await service.resume(run_id=run.id, request=request)

    assert result.resumed is True
    assert run.latency_ms == 8_000
    telemetry = AgentTelemetry.model_validate(run.telemetry)
    assert telemetry.elapsed_ms == 8_000
    assert telemetry.resume_approval is not None
    assert telemetry.resume_approval["resume_segment_elapsed_ms"] == 3_000
    assert telemetry.graph_state is not None
    checkpoint = GraphState.model_validate_json(json.dumps(telemetry.graph_state))
    assert checkpoint.budget.elapsed_ms == 8_000


@pytest.mark.asyncio
async def test_approval_resume_preserves_graph_and_domain_context_hashes() -> None:
    run, request, _ = _approval_fixture(
        approval_context_hash=APPROVAL_CONTEXT_HASH,
    )
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    approval = ObservedApproval("approved")
    service = GraphAgentRunService(
        repository,  # type: ignore[arg-type]
        approval_resume=approval,  # type: ignore[arg-type]
    )

    result = await service.resume(run_id=run.id, request=request)

    assert result.resumed is True
    assert run.context_hash == INTERRUPT_SIGNATURE
    assert approval.calls[0]["context_hash"] == APPROVAL_CONTEXT_HASH
    telemetry = AgentTelemetry.model_validate(run.telemetry)
    assert telemetry.resume_approval is not None
    assert telemetry.resume_approval["context_hash"] == INTERRUPT_SIGNATURE
    assert (
        telemetry.resume_approval["approval_context_hash"]
        == APPROVAL_CONTEXT_HASH
    )
    repository.actor_role_override = "approver"
    revalidated = await service._revalidate_resumed_approval(
        run=run,
        telemetry=telemetry,
    )
    assert revalidated.status == "approved"
    assert approval.calls[1]["context_hash"] == APPROVAL_CONTEXT_HASH


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approval_status", "terminal_state", "resume_event", "final_event"),
    [
        ("stale", "stale", "run.resume_blocked", "run.stopped"),
        ("rejected", "success", "run.resume_resolved", "run.completed"),
    ],
)
async def test_noncontinuing_approval_resume_clears_checkpoint_and_is_idempotent(
    approval_status: str,
    terminal_state: str,
    resume_event: str,
    final_event: str,
) -> None:
    run, request, _ = _approval_fixture()
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    approval = ObservedApproval(approval_status)
    service = GraphAgentRunService(
        repository,  # type: ignore[arg-type]
        approval_resume=approval,  # type: ignore[arg-type]
    )

    first = await service.resume(run_id=run.id, request=request)
    replay = await service.resume(run_id=run.id, request=request)
    assert request.approval is not None
    changed_approval = request.approval.model_copy(update={"preview_hash": "5" * 64})
    changed_request = request.model_copy(update={"approval": changed_approval})

    assert first.resumed is False
    assert first.terminal_state == terminal_state
    assert replay.resumed is False
    assert replay.idempotent_replay is True
    assert run.terminal_state == terminal_state
    assert approval.decision_commits == 0
    telemetry = AgentTelemetry.model_validate(run.telemetry)
    assert telemetry.graph_state is None
    assert telemetry.checkpoint is None
    assert telemetry.resume_kind is None
    assert telemetry.resume_approval is None
    assert AgentRunPayload.model_validate(run.result).pending_interrupt is None
    event_names = [step.output_snapshot.get("event_name") for step in repository.steps]
    assert event_names.count(resume_event) == 1
    assert event_names.count(final_event) == 1
    assert "run.resumed" not in event_names

    with pytest.raises(AgentResumeError) as caught:
        await service.resume(run_id=run.id, request=changed_request)
    assert caught.value.code == "resume_idempotency_conflict"


@pytest.mark.asyncio
async def test_resume_is_idempotent_and_rejects_changed_payload_for_same_key() -> None:
    run, request = _clarification_fixture()
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    service = GraphAgentRunService(repository)  # type: ignore[arg-type]

    first = await service.resume(run_id=run.id, request=request)
    replay = await service.resume(run_id=run.id, request=request)
    assert request.clarification is not None
    changed_context = request.clarification.context.model_copy(update={"site_id": uuid4()})
    changed_clarification = request.clarification.model_copy(
        update={"context": changed_context}
    )
    conflict = request.model_copy(
        update={"clarification": changed_clarification}
    )

    assert first.resumed is True
    assert replay.idempotent_replay is True
    assert replay.resumed is True
    assert repository.commits == 1
    assert len(repository.resumed_steps()) == 1
    with pytest.raises(AgentResumeError) as caught:
        await service.resume(run_id=run.id, request=conflict)
    assert caught.value.code == "resume_idempotency_conflict"
    assert repository.commits == 1
    assert len(repository.resumed_steps()) == 1


@pytest.mark.asyncio
async def test_resume_idempotency_lookup_pages_beyond_first_500_steps() -> None:
    run, request = _clarification_fixture()
    repository = InMemoryResumeRepository(run, request.interrupt_sequence)
    service = GraphAgentRunService(repository)  # type: ignore[arg-type]

    first = await service.resume(run_id=run.id, request=request)
    resumed_step = repository.resumed_steps()[0]
    resumed_step.sequence = 501
    repository.steps = [
        SimpleNamespace(
            sequence=sequence,
            step_type="node",
            output_snapshot={"event_name": "node.completed", "data": {}},
        )
        for sequence in range(1, 501)
    ] + [resumed_step]

    replay = await service.resume(run_id=run.id, request=request)

    assert first.resumed is True
    assert replay.idempotent_replay is True
    assert replay.resumed is True
    assert repository.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stale_latest_sequence", "signature", "expected_code"),
    [
        (9, INTERRUPT_SIGNATURE, "stale_interrupt_cursor"),
        (4, "f" * 64, "resume_analysis_signature_mismatch"),
    ],
)
async def test_stale_cursor_and_signature_fail_closed_before_resume_commit(
    stale_latest_sequence: int,
    signature: str,
    expected_code: str,
) -> None:
    run, request = _clarification_fixture(interrupt_sequence=4)
    repository = InMemoryResumeRepository(run, stale_latest_sequence)
    service = GraphAgentRunService(repository)  # type: ignore[arg-type]
    stale_request = request.model_copy(update={"analysis_signature": signature})

    with pytest.raises(AgentResumeError) as caught:
        await service.resume(run_id=run.id, request=stale_request)

    assert caught.value.code == expected_code
    assert run.terminal_state == "needs_clarification"
    assert run.stage == "interrupted"
    assert repository.commits == 0
    assert repository.resumed_steps() == []
