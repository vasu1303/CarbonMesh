"""Active LangGraph agent runtime with structured planning and durable traces."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from contextlib import aclosing
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.core.observability import begin_external_usage, take_external_usage, traced_operation
from app.db.models.ai import AgentRun
from app.modules.agents.budget import (
    RuntimeBudgetCounter,
    RuntimeBudgetExhausted,
    new_budget,
)
from app.modules.agents.context import (
    build_frozen_context,
    extract_query_context_hints,
    freeze_runtime_context,
    freeze_runtime_context_from_hash,
    workflow_for_modules,
)
from app.modules.agents.graph_adapter import RegistryGraphToolInvoker
from app.modules.agents.graph_contracts import (
    AgentGraphState,
    ApprovalInterrupt,
    BudgetState,
    ContextEnvelope,
    ExecutionPlan,
    GraphState,
    StageRepairCount,
    ToolInvocation,
    ToolInvoker,
    ToolResult,
    budget_limits_for,
)
from app.modules.agents.graphs import compile_agent_graphs
from app.modules.agents.llm.base import AIModel
from app.modules.agents.llm.errors import AIConfigurationError, AIProviderError
from app.modules.agents.llm.factory import build_ai_model
from app.modules.agents.planning import (
    PlannerSelection,
    PolicyBlockedError,
    build_execution_plan,
    planning_system_instruction,
    planning_user_content,
    preflight_policy,
)
from app.modules.agents.repository import (
    MAX_AGENT_RUN_STEP_PAGE_SIZE,
    AgentRunRepository,
    ContextReferenceNotFoundError,
    NaturalLanguageContextAmbiguousError,
)
from app.modules.agents.resume import ApprovalResumePort, ApprovalResumeState
from app.modules.agents.schemas import (
    AgentContextRequest,
    AgentFact,
    AgentJudgment,
    AgentQueryAccepted,
    AgentQueryRequest,
    AgentResumeRequest,
    AgentResumeResult,
    AgentRunPayload,
    AgentRunResult,
    AgentTelemetry,
    ApprovalRequirement,
    EventName,
    FrozenContextEnvelope,
    PendingInterrupt,
    StageTelemetry,
    Workflow,
)
from app.modules.agents.step_recorder import AgentStepRecorder
from app.modules.agents.structured_output import (
    ProviderLifecycleEvent,
    StructuredModelRunner,
    StructuredOutputValidationError,
)
from app.modules.agents.sustainability import (
    SUSTAINABILITY_PROXY_METHOD,
    SUSTAINABILITY_PROXY_VERSION,
    estimate_sustainability_proxy,
)
from app.modules.agents.tool_ports import CarbonMeshAgentToolServicePort
from app.modules.agents.tools import AgentToolRegistry

AGENT_EXECUTION_LEASE_SECONDS = 90


class AgentModelFactory(Protocol):
    def __call__(self) -> AIModel: ...


class GraphAgentRunNotFoundError(LookupError):
    pass


class AgentResumeError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class _RuntimeBudgetInvoker:
    def __init__(
        self,
        delegate: ToolInvoker,
        budget: RuntimeBudgetCounter,
    ) -> None:
        self._delegate = delegate
        self._budget = budget

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        try:
            self._budget.consume_tool()
        except RuntimeBudgetExhausted as error:
            return ToolResult(
                status="budget_exhausted",
                code=f"{error.dimension}_budget_exhausted",
            )
        remaining_seconds = self._budget.remaining_seconds
        if remaining_seconds <= 0:
            return ToolResult(
                status="budget_exhausted",
                code="latency_budget_exhausted",
            )
        try:
            async with asyncio.timeout(remaining_seconds):
                result = await self._delegate.invoke(invocation)
        except TimeoutError:
            # The registry adapter recovers cancellation while still holding the
            # persistence lock, before any queued checkpoint can use the session.
            return ToolResult(
                status="budget_exhausted",
                code="latency_budget_exhausted",
            )
        try:
            self._budget.check_deadline()
        except RuntimeBudgetExhausted:
            return ToolResult(
                status="budget_exhausted",
                code="latency_budget_exhausted",
            )
        return result


class GraphAgentRunService:
    """Persist and execute one bounded, provider-backed LangGraph run."""

    def __init__(
        self,
        repository: AgentRunRepository,
        *,
        model_factory: AgentModelFactory | None = None,
        tool_registry_factory: Callable[[], AgentToolRegistry] | None = None,
        approval_resume: ApprovalResumePort | None = None,
    ) -> None:
        self._repository = repository
        self._model_factory = model_factory or build_ai_model
        self._tool_registry_factory = tool_registry_factory or (
            lambda: AgentToolRegistry(CarbonMeshAgentToolServicePort())
        )
        self._approval_resume = approval_resume

    async def start(
        self,
        request: AgentQueryRequest,
        *,
        trace_id: str,
    ) -> AgentQueryAccepted:
        started_clock = perf_counter()
        start_budget = new_budget("single")
        request = await self._resolve_previous_context(request)
        resolved_request, _, context_mismatches = await self._resolve_query_context(
            request
        )
        references = await self._repository.resolve_context_references(
            company_id=resolved_request.context.company_id,
            actor_id=resolved_request.context.actor_id,
            **_reference_scope(resolved_request.context),
        )
        run_id = uuid4()
        started_at = datetime.now(UTC)
        provisional_context = build_frozen_context(
            request_context=resolved_request.context,
            actor_role=references.actor_role,
            query=request.query,
            workflow="planning",
        )
        telemetry = AgentTelemetry(
            trace_id=trace_id,
            analysis_signature=provisional_context.analysis_signature,
            orchestrator_version="carbonmesh.langgraph.v1",
            elapsed_ms=0,
        )
        run = AgentRun(
            id=run_id,
            company_id=request.context.company_id,
            actor_id=request.context.actor_id,
            trace_id=trace_id,
            workflow="planning",
            stage="queued",
            terminal_state="running",
            context_envelope=provisional_context.model_dump(mode="json"),
            context_hash=provisional_context.analysis_signature,
            plan=None,
            result=AgentRunPayload(
                message="The bounded LangGraph workflow is queued for execution."
            ).model_dump(mode="json"),
            telemetry=telemetry.model_dump(mode="json"),
            started_at=started_at,
        )
        await self._repository.add(run)
        recorder = AgentStepRecorder(
            self._repository,
            company_id=run.company_id,
            run_id=run.id,
        )
        await recorder.record(
            step_type="context",
            event_name="run.started",
            status="completed",
            graph_name="orchestrator",
            node_name="run.start",
            input_summary={
                "request_hash": provisional_context.request_hash,
                "analysis_signature": provisional_context.analysis_signature,
            },
            data={
                "run_id": str(run.id),
                "trace_id": trace_id,
                "orchestrator_version": "carbonmesh.langgraph.v1",
            },
        )
        if context_mismatches:
            await self._interrupt_for_clarification(
                run=run,
                recorder=recorder,
                fields=list(context_mismatches),
                code="context_query_mismatch",
                budget=start_budget,
                started_clock=started_clock,
            )
        return AgentQueryAccepted(run_id=run.id, trace_id=trace_id)

    @traced_operation("agent.execute")
    async def execute(self, *, request: AgentQueryRequest, run_id: UUID) -> None:
        begin_external_usage()
        claim_id = await self._acquire_execution_claim(
            company_id=request.context.company_id,
            run_id=run_id,
        )
        if claim_id is None:
            return
        run = await self._repository.get(company_id=request.context.company_id, run_id=run_id)
        if run is None or run.terminal_state != "running":
            await self._release_execution_claim(
                company_id=request.context.company_id,
                run_id=run_id,
                claim_id=claim_id,
            )
            return
        provisional = FrozenContextEnvelope.model_validate(run.context_envelope)
        # Execute exactly the complete context frozen by start(), including a
        # bounded previous-run merge. Never re-read a mutable conversation here.
        persisted_context = AgentContextRequest.model_validate({
            key: getattr(provisional, key)
            for key in AgentContextRequest.model_fields
        })
        resolved_from_query = (
            request.previous_run_id is None
            and (persisted_context.site_id != request.context.site_id
                 or persisted_context.reporting_period_id != request.context.reporting_period_id)
        )
        request = request.model_copy(update={"context": persisted_context})
        if not resolved_from_query:
            request, resolved_from_query, _ = await self._resolve_query_context(
                request
            )
        recorder = AgentStepRecorder(
            self._repository,
            company_id=run.company_id,
            run_id=run.id,
        )
        # The planner cannot know whether the request qualifies for the golden
        # profile until its structured selection is validated.  Start under
        # the stricter single-module deadline and promote only a selected
        # multi-module plan, retaining this counter's original start clock.
        budget = new_budget("single")
        started_clock = perf_counter()
        try:
            await self._execute_loaded(
                run=run,
                request=request,
                recorder=recorder,
                budget=budget,
                started_clock=started_clock,
                claim_id=claim_id,
                include_entity_resolution=resolved_from_query,
            )
        except Exception:  # noqa: BLE001 - persist a credential-safe terminal failure
            await self._repository.rollback()
            persisted = await self._repository.get(
                company_id=request.context.company_id,
                run_id=run_id,
            )
            if persisted is not None and persisted.terminal_state == "running":
                await self._stop(
                    run=persisted,
                    recorder=recorder,
                    terminal_state="validation_failed",
                    code="agent_runtime_failed",
                    message="The bounded agent runtime failed safely.",
                    budget=budget,
                    started_clock=started_clock,
                )
        finally:
            await self._release_execution_claim(
                company_id=request.context.company_id,
                run_id=run_id,
                claim_id=claim_id,
            )

    async def _resolve_previous_context(self, request: AgentQueryRequest) -> AgentQueryRequest:
        if request.previous_run_id is None:
            return request
        previous = await self._repository.get(
            company_id=request.context.company_id, run_id=request.previous_run_id,
        )
        if previous is None or previous.actor_id != request.context.actor_id:
            raise ContextReferenceNotFoundError("previous_run")
        if previous.terminal_state == "running":
            raise ContextReferenceNotFoundError("completed_previous_run")
        frozen = FrozenContextEnvelope.model_validate(previous.context_envelope)
        values = {key: getattr(frozen, key) for key in AgentContextRequest.model_fields}
        explicit = request.context.model_dump(exclude_unset=True)
        for key in ("constraints", "dispatch_constraints"):
            if key in explicit:
                explicit[key] = {**getattr(frozen, key).model_dump(), **explicit[key]}
        values.update(explicit)
        # A new question gets a new immutable context/signature. Derived
        # approvals/scenarios must never survive changed scope or constraints.
        for key in ("site_id", "reporting_period_id", "material_scope", "constraints", "dispatch_constraints"):
            if key in explicit and explicit[key] != frozen.model_dump()[key]:
                for artifact in ("procurement_scenario_id", "dispatch_scenario_id", "disclosure_draft_id"):
                    values[artifact] = None
                if key in {"site_id", "reporting_period_id", "material_scope"}:
                    values["carbon_measurement_id"] = None
                    values["activity_record_ids"] = []
                    if "fresh_inputs" not in explicit:
                        values["fresh_inputs"] = None
        return request.model_copy(update={"context": AgentContextRequest.model_validate(values)})

    async def _planner_content(self, query: str, context: AgentContextRequest) -> str:
        catalog_reader = getattr(self._repository, "planning_catalog", None)
        catalog = await catalog_reader(company_id=context.company_id) if catalog_reader else {}
        return planning_user_content(
            query, available_context_fields=_available_context_fields(context),
            semantic_catalog=catalog,
            frozen_scope={
                "metric_keys": context.metric_keys, "material_scope": context.material_scope,
                "constraints": context.constraints.model_dump(mode="json", exclude_none=True),
                "dispatch_constraints": context.dispatch_constraints.model_dump(mode="json", exclude_none=True),
                "grid_source_mode": context.grid_source_mode,
                "creates_domain_artifacts": context.fresh_inputs is not None,
            },
        )

    async def _resolve_query_context(
        self,
        request: AgentQueryRequest,
    ) -> tuple[AgentQueryRequest, bool, tuple[str, ...]]:
        """Resolve exact query hints without retaining the raw prompt.

        Missing IDs are filled only by an exact tenant-scoped match. An explicit
        ID that contradicts an exact query reference is cleared and returned as
        a clarification field; neither source silently overrides the other.
        """

        hints = extract_query_context_hints(request.query)
        site_reference = hints.site_reference
        period_start = hints.period_start
        period_end = hints.period_end
        if site_reference is None and period_start is None:
            return request, False, ()
        resolver = getattr(self._repository, "resolve_natural_language_context", None)
        if resolver is None:
            return request, False, ()
        try:
            resolved = await resolver(
                company_id=request.context.company_id,
                site_reference=site_reference,
                period_start=period_start,
                period_end=period_end,
            )
        except NaturalLanguageContextAmbiguousError:
            mismatch_fields = tuple(
                field
                for field, present, explicit in (
                    ("context.site_id", site_reference is not None, request.context.site_id),
                    (
                        "context.reporting_period_id",
                        period_start is not None,
                        request.context.reporting_period_id,
                    ),
                )
                if present and explicit is not None
            )
            if not mismatch_fields:
                return request, False, ()
            context = request.context.model_copy(
                update={
                    field.removeprefix("context."): None
                    for field in mismatch_fields
                }
            )
            return request.model_copy(update={"context": context}), True, mismatch_fields

        mismatch_fields: list[str] = []
        updates: dict[str, UUID | None] = {}
        if site_reference is not None:
            if request.context.site_id is not None and (
                resolved.site_id is None or request.context.site_id != resolved.site_id
            ):
                mismatch_fields.append("context.site_id")
                updates["site_id"] = None
            elif request.context.site_id is None and resolved.site_id is not None:
                updates["site_id"] = resolved.site_id
        if period_start is not None:
            if request.context.reporting_period_id is not None and (
                resolved.reporting_period_id is None
                or request.context.reporting_period_id != resolved.reporting_period_id
            ):
                mismatch_fields.append("context.reporting_period_id")
                updates["reporting_period_id"] = None
            elif (
                request.context.reporting_period_id is None
                and resolved.reporting_period_id is not None
            ):
                updates["reporting_period_id"] = resolved.reporting_period_id
        context = request.context.model_copy(update=updates)
        changed = context != request.context
        return request.model_copy(update={"context": context}), changed, tuple(mismatch_fields)

    async def resume(
        self,
        *,
        run_id: UUID,
        request: AgentResumeRequest,
    ) -> AgentResumeResult:
        """Validate and persist one durable interrupt resume.

        The accepted checkpoint is written before the caller schedules graph
        execution, so a process restart never depends on in-memory state.
        Approval decisions are only observed here; they remain owned by the
        generic approval service and are never committed by an agent node.
        """

        resume_budget = new_budget("resume")
        run = await self._repository.get_for_update(
            company_id=request.company_id,
            run_id=run_id,
        )
        if run is None:
            raise GraphAgentRunNotFoundError

        resolved_from_query = False
        if (
            request.clarification is not None
            and request.clarification.clarified_query is not None
        ):
            (
                clarified_request,
                resolved_from_query,
                context_mismatches,
            ) = await self._resolve_query_context(
                AgentQueryRequest(
                    query=request.clarification.clarified_query,
                    context=request.clarification.context,
                )
            )
            if context_mismatches:
                raise AgentResumeError(
                    "context_query_mismatch",
                    "The clarified query contradicts its explicit site or period.",
                    status_code=422,
                )
            request = request.model_copy(
                update={
                    "clarification": request.clarification.model_copy(
                        update={"context": clarified_request.context}
                    )
                }
            )
        clarification_context = (
            request.clarification.context if request.clarification is not None else None
        )
        references = await self._repository.resolve_context_references(
            company_id=request.company_id,
            actor_id=request.actor_id,
            **(_reference_scope(clarification_context) if clarification_context else {}),
        )
        key_hash, payload_hash = _resume_request_hashes(request)
        prior = await self._find_resume_attempt(
            company_id=request.company_id,
            run_id=run_id,
            idempotency_key_hash=key_hash,
        )
        if prior is not None:
            if prior != payload_hash:
                raise AgentResumeError(
                    "resume_idempotency_conflict",
                    "The idempotency key was already used with another resume payload.",
                )
            return AgentResumeResult(
                run_id=run.id,
                trace_id=run.trace_id,
                terminal_state=run.terminal_state,
                resumed=run.terminal_state == "running",
                idempotent_replay=True,
            )

        payload = AgentRunPayload.model_validate(
            run.result or {"message": "The run has no resumable interrupt."}
        )
        pending = payload.pending_interrupt
        self._validate_resume_identity(run=run, pending=pending, request=request)
        latest_interrupt = await self._repository.latest_interrupt(
            company_id=request.company_id,
            run_id=run.id,
        )
        if latest_interrupt is None or latest_interrupt.sequence != request.interrupt_sequence:
            raise AgentResumeError(
                "stale_interrupt_cursor",
                "The requested interrupt is no longer the latest durable checkpoint.",
            )

        recorder = AgentStepRecorder(
            self._repository,
            company_id=run.company_id,
            run_id=run.id,
        )
        if pending.kind == "clarification":
            if request.clarification is None:
                raise AgentResumeError(
                    "resume_payload_kind_mismatch",
                    "This run requires a clarification payload.",
                    status_code=422,
                )
            return await self._resume_clarification(
                run=run,
                payload=payload,
                pending=pending,
                request=request,
                actor_role=references.actor_role,
                recorder=recorder,
                idempotency_key_hash=key_hash,
                resume_payload_hash=payload_hash,
                include_entity_resolution=resolved_from_query,
                resume_budget=resume_budget,
            )

        if request.approval is None:
            raise AgentResumeError(
                "resume_payload_kind_mismatch",
                "This run requires an approval payload.",
                status_code=422,
            )
        return await self._resume_approval(
            run=run,
            payload=payload,
            pending=pending,
            request=request,
            actor_role=references.actor_role,
            recorder=recorder,
            idempotency_key_hash=key_hash,
            resume_payload_hash=payload_hash,
            resume_budget=resume_budget,
        )

    @traced_operation("agent.resume")
    async def execute_resumed(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        claim_id: UUID | None = None,
    ) -> None:
        """Continue only from a checkpoint already committed by :meth:`resume`."""
        begin_external_usage()
        owned_claim_id = await self._acquire_execution_claim(
            company_id=company_id,
            run_id=run_id,
            claim_id=claim_id,
        )
        if owned_claim_id is None:
            return
        run = await self._repository.get(company_id=company_id, run_id=run_id)
        if (
            run is None
            or run.terminal_state != "running"
            or run.stage
            not in {"planned", "resumed_clarification", "resumed_approval", "graph"}
        ):
            await self._release_execution_claim(
                company_id=company_id,
                run_id=run_id,
                claim_id=owned_claim_id,
            )
            return
        recorder = AgentStepRecorder(
            self._repository,
            company_id=run.company_id,
            run_id=run.id,
        )
        budget = new_budget("resume")
        started_clock = perf_counter()
        try:
            telemetry = AgentTelemetry.model_validate(run.telemetry)
            if telemetry.graph_state is not None:
                state = _load_graph_state(telemetry.graph_state)
            elif (
                run.stage == "planned"
                and run.plan is not None
                and telemetry.graph_context is not None
            ):
                plan = _load_execution_plan(run.plan)
                graph_budget = BudgetState.for_profile(plan.profile).model_copy(
                    update={
                        "model_calls": run.model_calls,
                        "tool_calls": run.tool_calls,
                        "elapsed_ms": max(run.latency_ms, telemetry.elapsed_ms),
                        "repairs_by_stage": (
                            (
                                StageRepairCount(
                                    stage_id="intent_planning",
                                    count=telemetry.repairs,
                                ),
                            )
                            if telemetry.repairs
                            else ()
                        ),
                    }
                )
                state = AgentGraphState.initial(
                    run_id=run.id,
                    context=_load_graph_context(telemetry.graph_context),
                    plan=plan,
                    budget=graph_budget,
                )
            else:
                raise ValueError("persisted graph checkpoint is unavailable")
            # Approval validation consumes no graph tools. Continue against the
            # original cumulative plan budget so sequential module approvals do
            # not reset or double-count the bounded execution.
            budget = _resume_execution_budget(
                state=state,
                persisted_elapsed_ms=run.latency_ms,
                model_calls=run.model_calls,
                repairs=telemetry.repairs,
                approval_resume=telemetry.resume_kind == "approval",
                resume_segment_elapsed_ms=(
                    int(telemetry.resume_approval.get("resume_segment_elapsed_ms", 0))
                    if telemetry.resume_approval is not None
                    else 0
                ),
            )
            try:
                budget.check_deadline()
            except RuntimeBudgetExhausted as error:
                await self._stop(
                    run=run,
                    recorder=recorder,
                    terminal_state="budget_exhausted",
                    code=f"{error.dimension}_budget_exhausted",
                    message="The resumed run exhausted its cumulative latency budget.",
                    budget=budget,
                    started_clock=started_clock,
                )
                return
            if telemetry.resume_kind == "approval":
                try:
                    async with asyncio.timeout(budget.remaining_seconds):
                        observed = await self._revalidate_resumed_approval(
                            run=run,
                            telemetry=telemetry,
                        )
                    budget.check_deadline()
                except (TimeoutError, RuntimeBudgetExhausted):
                    # Cancelling an approval read can invalidate asyncpg's
                    # transaction. Recover and refresh the run before writing
                    # the typed deadline result through the same session.
                    await recorder.recover_transaction()
                    await self._stop(
                        run=run,
                        recorder=recorder,
                        terminal_state="budget_exhausted",
                        code="latency_budget_exhausted",
                        message=(
                            "The approval continuation exceeded its bounded resume "
                            "latency."
                        ),
                        budget=budget,
                        started_clock=started_clock,
                    )
                    return
                if observed.status != "approved":
                    await self._stop(
                        run=run,
                        recorder=recorder,
                        terminal_state="stale",
                        code=observed.code,
                        message="The approval changed before graph continuation.",
                        budget=budget,
                        started_clock=started_clock,
                    )
                    return
            persistence_lock = asyncio.Lock()
            invoker = _RuntimeBudgetInvoker(
                RegistryGraphToolInvoker(
                    self._tool_registry_factory(),
                    recorder=recorder,
                    persistence_lock=persistence_lock,
                ),
                budget,
            )
            graph = compile_agent_graphs(invoker).orchestrator
            final = await self._run_graph_durably(
                run=run,
                recorder=recorder,
                graph=graph,
                initial=state,
                claim_id=owned_claim_id,
                runtime_budget=budget,
                persistence_lock=persistence_lock,
            )
            try:
                budget.check_deadline()
            except RuntimeBudgetExhausted as error:
                await self._stop(
                    run=run,
                    recorder=recorder,
                    terminal_state="budget_exhausted",
                    code=f"{error.dimension}_budget_exhausted",
                    message="The resumed run exhausted its cumulative latency budget.",
                    budget=budget,
                    started_clock=started_clock,
                )
                return
            await self._finalize_graph(
                run=run,
                recorder=recorder,
                state=final,
                budget=budget,
                started_clock=started_clock,
            )
        except Exception:  # noqa: BLE001 - persist a safe typed resume failure
            await self._repository.rollback()
            persisted = await self._repository.get(company_id=company_id, run_id=run_id)
            if persisted is not None and persisted.terminal_state == "running":
                await self._stop(
                    run=persisted,
                    recorder=recorder,
                    terminal_state="validation_failed",
                    code="resume_execution_failed",
                    message="The durable graph checkpoint could not be resumed safely.",
                    budget=budget,
                    started_clock=started_clock,
                )
        finally:
            await self._release_execution_claim(
                company_id=company_id,
                run_id=run_id,
                claim_id=owned_claim_id,
            )

    async def fail_orphaned_execution(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        claim_id: UUID,
    ) -> None:
        """Terminate an old running job that has no replayable plan/checkpoint."""

        owned_claim_id = await self._acquire_execution_claim(
            company_id=company_id,
            run_id=run_id,
            claim_id=claim_id,
        )
        if owned_claim_id is None:
            return
        try:
            run = await self._repository.get(company_id=company_id, run_id=run_id)
            if run is None or run.terminal_state != "running":
                return
            telemetry = AgentTelemetry.model_validate(run.telemetry)
            if telemetry.graph_state is not None:
                return
            budget = new_budget("golden")
            budget.model_calls = run.model_calls
            budget.tool_calls = run.tool_calls
            budget.repairs = telemetry.repairs
            await self._stop(
                run=run,
                recorder=AgentStepRecorder(
                    self._repository,
                    company_id=company_id,
                    run_id=run_id,
                ),
                terminal_state="validation_failed",
                code="orphan_checkpoint_unavailable",
                message=(
                    "The interrupted agent job had no safe durable checkpoint and was "
                    "stopped without replaying side effects."
                ),
                budget=budget,
                started_clock=perf_counter(),
            )
        finally:
            await self._release_execution_claim(
                company_id=company_id,
                run_id=run_id,
                claim_id=owned_claim_id,
            )

    async def _acquire_execution_claim(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        claim_id: UUID | None = None,
    ) -> UUID | None:
        claim = getattr(self._repository, "try_claim_execution", None)
        renew = getattr(self._repository, "renew_execution_claim", None)
        if claim is None or renew is None:
            return claim_id or uuid4()
        if claim_id is None:
            observed = await claim(
                company_id=company_id,
                run_id=run_id,
                lease_seconds=AGENT_EXECUTION_LEASE_SECONDS,
            )
        else:
            observed = await renew(
                company_id=company_id,
                run_id=run_id,
                claim_id=claim_id,
                lease_seconds=AGENT_EXECUTION_LEASE_SECONDS,
            )
        await self._repository.commit()
        return observed.claim_id if observed is not None else None

    async def _renew_execution_claim(
        self,
        *,
        run: AgentRun,
        claim_id: UUID,
    ) -> None:
        renew = getattr(self._repository, "renew_execution_claim", None)
        if renew is None:
            return
        observed = await renew(
            company_id=run.company_id,
            run_id=run.id,
            claim_id=claim_id,
            lease_seconds=AGENT_EXECUTION_LEASE_SECONDS,
        )
        if observed is None:
            raise RuntimeError("agent execution claim was lost")

    async def _release_execution_claim(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        claim_id: UUID,
    ) -> None:
        release = getattr(self._repository, "release_execution_claim", None)
        if release is None:
            return
        try:
            await self._repository.rollback()
            await release(
                company_id=company_id,
                run_id=run_id,
                claim_id=claim_id,
            )
            await self._repository.commit()
        except Exception:  # noqa: BLE001 - an expired lease remains safely reclaimable
            await self._repository.rollback()

    async def _resume_clarification(
        self,
        *,
        run: AgentRun,
        payload: AgentRunPayload,
        pending: PendingInterrupt,
        request: AgentResumeRequest,
        actor_role: str,
        recorder: AgentStepRecorder,
        idempotency_key_hash: str,
        resume_payload_hash: str,
        include_entity_resolution: bool,
        resume_budget: RuntimeBudgetCounter,
    ) -> AgentResumeResult:
        clarification = request.clarification
        if clarification is None:  # pragma: no cover - guarded by caller
            raise AgentResumeError("resume_payload_kind_mismatch", "Clarification is required.")
        context = clarification.context
        if (
            context.company_id != request.company_id
            or context.actor_id != request.actor_id
            or context.actor_id != run.actor_id
        ):
            raise AgentResumeError(
                "resume_context_mismatch",
                "Clarified context must preserve the tenant and requesting run actor.",
                status_code=422,
            )
        frozen = FrozenContextEnvelope.model_validate(run.context_envelope)
        if actor_role != frozen.actor_role:
            raise AgentResumeError(
                "resume_actor_role_changed",
                "The actor role changed after the context was frozen.",
            )
        if not _preserves_frozen_context(frozen, context):
            raise AgentResumeError(
                "resume_context_scope_changed",
                "Clarification may fill missing fields but cannot replace frozen scope or constraints.",
            )
        context = _merge_frozen_context(frozen, context)
        if run.plan is None:
            if clarification.clarified_query is None:
                raise AgentResumeError(
                    "clarified_query_required",
                    "Planner clarification requires a clarified query for safe replanning.",
                    status_code=422,
                )
            return await self._resume_planner_clarification(
                run=run,
                payload=payload,
                pending=pending,
                context=context,
                clarified_query=clarification.clarified_query,
                actor_role=frozen.actor_role,
                recorder=recorder,
                idempotency_key_hash=idempotency_key_hash,
                resume_payload_hash=resume_payload_hash,
                include_entity_resolution=include_entity_resolution,
                resume_budget=resume_budget,
            )
        if clarification.clarified_query is not None:
            raise AgentResumeError(
                "clarified_query_unexpected",
                "A frozen execution plan accepts context fields but cannot be replanned.",
                status_code=422,
            )
        plan = _load_execution_plan(run.plan)
        missing_fields = _required_context_fields(plan, context)
        if missing_fields:
            raise AgentResumeError(
                "clarification_incomplete",
                "The clarification payload is still missing required frozen-context fields.",
                status_code=422,
            )
        api_context, graph_context = freeze_runtime_context_from_hash(
            request_context=context,
            actor_role=frozen.actor_role,
            request_hash=frozen.request_hash,
            plan=plan,
        )
        telemetry = AgentTelemetry.model_validate(run.telemetry)
        if telemetry.graph_state is None:
            graph_budget = BudgetState.for_profile(plan.profile).model_copy(
                update={
                    "model_calls": run.model_calls,
                    "elapsed_ms": max(run.latency_ms, telemetry.elapsed_ms),
                    "repairs_by_stage": (
                        (
                            StageRepairCount(
                                stage_id="intent_planning",
                                count=telemetry.repairs,
                            ),
                        )
                        if telemetry.repairs
                        else ()
                    ),
                }
            )
        else:
            graph_budget = _load_graph_state(telemetry.graph_state).budget
        # Context-dependent outputs are never reused under a changed analysis
        # signature. Deterministic/idempotent services replay from the start of
        # the frozen plan while the original total budget remains consumed.
        graph_state = AgentGraphState.initial(
            run_id=run.id,
            context=graph_context,
            plan=plan,
            budget=graph_budget,
        )
        await self._accept_resume(
            run=run,
            payload=payload,
            recorder=recorder,
            graph_state=graph_state,
            api_context=api_context,
            event_data={
                "kind": "clarification",
                "interrupt_id": str(pending.interrupt_id),
            },
            idempotency_key_hash=idempotency_key_hash,
            resume_payload_hash=resume_payload_hash,
        )
        return AgentResumeResult(
            run_id=run.id,
            trace_id=run.trace_id,
            terminal_state="running",
            resumed=True,
        )

    async def _resume_planner_clarification(
        self,
        *,
        run: AgentRun,
        payload: AgentRunPayload,
        pending: PendingInterrupt,
        context: AgentContextRequest,
        clarified_query: str,
        actor_role: str,
        recorder: AgentStepRecorder,
        idempotency_key_hash: str,
        resume_payload_hash: str,
        include_entity_resolution: bool,
        resume_budget: RuntimeBudgetCounter,
    ) -> AgentResumeResult:
        """Replan an ambiguous intent without storing the clarified prompt."""

        try:
            preflight_policy(clarified_query)
        except PolicyBlockedError as error:
            raise AgentResumeError(
                error.code,
                "The clarified request was blocked by the bounded agent policy.",
                status_code=403,
            ) from error

        prior_telemetry = AgentTelemetry.model_validate(run.telemetry)
        prior_elapsed_ms = max(run.latency_ms, prior_telemetry.elapsed_ms)
        budget = resume_budget
        try:
            model = self._model_factory()
        except AIConfigurationError as error:
            raise AgentResumeError(
                getattr(error, "code", "ai_provider_unavailable"),
                "The configured model provider is unavailable for safe replanning.",
                status_code=503,
            ) from error

        async def observe_provider(event: ProviderLifecycleEvent) -> None:
            telemetry = AgentTelemetry.model_validate(run.telemetry)
            if event.event_name == "provider.started":
                run.model_calls += 1
                run.api_calls += 1
            elif event.event_name == "provider.completed":
                run.input_tokens += event.input_tokens
                run.output_tokens += event.output_tokens
            telemetry = telemetry.model_copy(
                update={
                    "provider": event.provider,
                    "provider_status": (
                        "completed"
                        if event.event_name == "provider.completed"
                        else "unavailable"
                        if event.event_name == "provider.failed"
                        else "configured"
                    ),
                    "model_id": event.model_id,
                    "model_calls": run.model_calls,
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "cached_input_tokens": (
                        telemetry.cached_input_tokens + event.cached_input_tokens
                        if event.event_name == "provider.completed"
                        else telemetry.cached_input_tokens
                    ),
                    "api_calls": run.api_calls,
                }
            )
            run.telemetry = telemetry.model_dump(mode="json")
            await recorder.record(
                step_type="provider",
                event_name=event.event_name,
                status=(
                    "completed"
                    if event.event_name == "provider.completed"
                    else "failed"
                    if event.event_name == "provider.failed"
                    else "running"
                ),
                graph_name="orchestrator",
                node_name=f"planner.resume.provider.attempt_{event.attempt}",
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                latency_ms=event.latency_ms,
                error_code=event.error_code,
                data={
                    "provider": event.provider,
                    "model_id": event.model_id,
                    "attempt": event.attempt,
                    "provider_request_id": event.provider_request_id,
                    "finish_reason": event.finish_reason,
                    "cached_input_tokens": event.cached_input_tokens,
                    "code": event.error_code,
                },
                commit=False,
            )

        try:
            planned = await StructuredModelRunner(
                model,
                observer=observe_provider,
            ).generate(
                PlannerSelection,
                stage="intent_replanning",
                system_instruction=planning_system_instruction(),
                user_content=await self._planner_content(clarified_query, context),
                budget=budget,
            )
        except (AIProviderError, AIConfigurationError) as error:
            raise AgentResumeError(
                getattr(error, "code", "ai_provider_unavailable"),
                "The selected model provider could not safely replan this run.",
                status_code=503,
            ) from error
        except StructuredOutputValidationError as error:
            raise AgentResumeError(
                error.code,
                "The clarified request did not produce a valid execution plan.",
                status_code=422,
            ) from error
        except RuntimeBudgetExhausted as error:
            raise AgentResumeError(
                f"{error.dimension}_budget_exhausted",
                "Clarification replanning exceeded the bounded resume budget.",
            ) from error

        selection = planned.value
        if selection.disposition != "execute":
            status_code = 403 if selection.disposition == "policy_blocked" else 422
            raise AgentResumeError(
                selection.reason_code,
                "The clarified request is still not an executable bounded workflow.",
                status_code=status_code,
            )
        try:
            plan = build_execution_plan(
                selection,
                include_entity_resolution=include_entity_resolution,
                fresh_inputs=context.fresh_inputs,
            )
        except ValueError as error:
            raise AgentResumeError(
                "execution_plan_policy_failed",
                "The clarified execution plan failed deterministic policy validation.",
                status_code=422,
            ) from error
        missing_fields = _required_context_fields(plan, context)
        if missing_fields:
            raise AgentResumeError(
                "clarification_incomplete",
                "The clarification still lacks required frozen-context fields: "
                + ", ".join(missing_fields),
                status_code=422,
            )

        api_context, graph_context = freeze_runtime_context(
            request_context=context,
            actor_role=actor_role,
            query=clarified_query,
            plan=plan,
        )
        current_telemetry = AgentTelemetry.model_validate(run.telemetry)
        cumulative_elapsed_ms = prior_elapsed_ms + budget.elapsed_ms
        run.latency_ms = cumulative_elapsed_ms
        graph_budget = BudgetState.for_profile(plan.profile).model_copy(
            update={
                "model_calls": run.model_calls,
                "elapsed_ms": cumulative_elapsed_ms,
                "repairs_by_stage": (
                    (
                        StageRepairCount(
                            stage_id="intent_planning",
                            count=current_telemetry.repairs,
                        ),
                    )
                    if current_telemetry.repairs
                    else ()
                ),
            }
        )
        graph_state = AgentGraphState.initial(
            run_id=run.id,
            context=graph_context,
            plan=plan,
            budget=graph_budget,
        )
        run.workflow = workflow_for_modules(plan.modules)
        run.plan = plan.model_dump(mode="json")
        run.telemetry = current_telemetry.model_copy(
            update={
                "analysis_signature": graph_context.analysis_signature,
                "planning_selection": selection.model_dump(mode="json"),
                "graph_context": graph_context.model_dump(mode="json"),
                "model_calls": run.model_calls,
                "elapsed_ms": cumulative_elapsed_ms,
                "stage_timings": [
                    StageTelemetry(stage="total", elapsed_ms=cumulative_elapsed_ms)
                ],
            }
        ).model_dump(mode="json")
        await recorder.record(
            step_type="planner",
            event_name="node.completed",
            status="completed",
            graph_name="orchestrator",
            node_name="planner.resume.structured",
            data={
                "disposition": selection.disposition,
                "modules": list(selection.modules),
                "reason_code": selection.reason_code,
                "plan_hash": plan.plan_hash,
            },
            commit=False,
        )
        await self._accept_resume(
            run=run,
            payload=payload,
            recorder=recorder,
            graph_state=graph_state,
            api_context=api_context,
            event_data={
                "kind": "clarification",
                "interrupt_id": str(pending.interrupt_id),
                "replanned": True,
                "plan_hash": plan.plan_hash,
            },
            idempotency_key_hash=idempotency_key_hash,
            resume_payload_hash=resume_payload_hash,
        )
        return AgentResumeResult(
            run_id=run.id,
            trace_id=run.trace_id,
            terminal_state="running",
            resumed=True,
        )

    async def _resume_approval(
        self,
        *,
        run: AgentRun,
        payload: AgentRunPayload,
        pending: PendingInterrupt,
        request: AgentResumeRequest,
        actor_role: str,
        recorder: AgentStepRecorder,
        idempotency_key_hash: str,
        resume_payload_hash: str,
        resume_budget: RuntimeBudgetCounter,
    ) -> AgentResumeResult:
        approval = request.approval
        if approval is None:  # pragma: no cover - guarded by caller
            raise AgentResumeError("resume_payload_kind_mismatch", "Approval is required.")
        if actor_role != "approver":
            raise AgentResumeError(
                "approval_resume_actor_forbidden",
                "An active approver must resume an approval interrupt.",
                status_code=403,
            )
        if pending.approval_id != approval.approval_id or pending.preview_hash != approval.preview_hash:
            raise AgentResumeError(
                "approval_resume_hash_mismatch",
                "The approval ID or preview hash does not match the durable interrupt.",
            )
        if self._approval_resume is None:
            raise AgentResumeError(
                "approval_resume_unavailable",
                "Approval revalidation is temporarily unavailable.",
                status_code=503,
            )
        try:
            resume_budget.check_deadline()
            async with asyncio.timeout(resume_budget.remaining_seconds):
                observed = await self._approval_resume.validate(
                    company_id=request.company_id,
                    approval_id=approval.approval_id,
                    preview_hash=approval.preview_hash,
                    analysis_signature=request.analysis_signature,
                    context_hash=(
                        pending.approval_context_hash
                        or pending.context_hash
                        or run.context_hash
                    ),
                    target_type=pending.target_type,
                    target_id=pending.target_id,
                )
            resume_budget.check_deadline()
        except (TimeoutError, RuntimeBudgetExhausted) as error:
            raise AgentResumeError(
                "latency_budget_exhausted",
                "Approval validation exceeded the bounded resume latency.",
            ) from error
        resume_segment_elapsed_ms = resume_budget.elapsed_ms
        if observed.status == "pending":
            return AgentResumeResult(
                run_id=run.id,
                trace_id=run.trace_id,
                terminal_state="approval_required",
                resumed=False,
            )

        telemetry = AgentTelemetry.model_validate(run.telemetry)
        persisted_state = (
            _load_graph_state(telemetry.graph_state)
            if telemetry.graph_state is not None
            else None
        )
        prior_elapsed_ms = max(
            0,
            run.latency_ms,
            telemetry.elapsed_ms,
            persisted_state.budget.elapsed_ms if persisted_state is not None else 0,
        )
        run.latency_ms = prior_elapsed_ms + resume_segment_elapsed_ms
        telemetry = telemetry.model_copy(
            update={
                "elapsed_ms": run.latency_ms,
                "stage_timings": [
                    StageTelemetry(stage="total", elapsed_ms=run.latency_ms)
                ],
            }
        )
        run.telemetry = telemetry.model_dump(mode="json")
        if observed.status == "stale" or telemetry.graph_state is None:
            code = observed.code if observed.status == "stale" else "resume_checkpoint_missing"
            await self._record_resume_event(
                recorder=recorder,
                pending=pending,
                idempotency_key_hash=idempotency_key_hash,
                resume_payload_hash=resume_payload_hash,
                data={"kind": "approval", "approval_status": observed.status},
                commit=False,
                event_name="run.resume_blocked",
                status="blocked",
            )
            stale_payload = payload.model_copy(
                update={
                    "message": "The approval resume is stale and cannot continue.",
                    "pending_interrupt": None,
                    "approval_requirement": ApprovalRequirement(),
                }
            )
            await self._persist_terminal(
                run=run,
                recorder=recorder,
                terminal_state="stale",
                code=code,
                payload=stale_payload,
                graph_state=None,
                budget=new_budget("resume"),
                started_clock=perf_counter(),
                final_event_name="run.stopped",
                clear_checkpoint=True,
            )
            return AgentResumeResult(
                run_id=run.id,
                trace_id=run.trace_id,
                terminal_state="stale",
                resumed=False,
            )

        if observed.status == "rejected":
            await self._record_resume_event(
                recorder=recorder,
                pending=pending,
                idempotency_key_hash=idempotency_key_hash,
                resume_payload_hash=resume_payload_hash,
                data={"kind": "approval", "approval_status": "rejected"},
                commit=False,
                event_name="run.resume_resolved",
                status="completed",
            )
            rejected_payload = payload.model_copy(
                update={
                    "message": "The external approval rejection was observed; no action was committed by the agent.",
                    "pending_interrupt": None,
                    "approval_requirement": ApprovalRequirement(),
                }
            )
            await self._persist_terminal(
                run=run,
                recorder=recorder,
                terminal_state="success",
                code=None,
                payload=rejected_payload,
                graph_state=None,
                budget=new_budget("resume"),
                started_clock=perf_counter(),
                final_event_name="run.completed",
                clear_checkpoint=True,
            )
            return AgentResumeResult(
                run_id=run.id,
                trace_id=run.trace_id,
                terminal_state="success",
                resumed=False,
            )

        graph_state = _prepare_resumed_graph_state(
            _load_graph_state(telemetry.graph_state),
            context=None,
            resolve_approval=True,
        )
        graph_state = _with_elapsed_ms(graph_state, run.latency_ms)
        await self._accept_resume(
            run=run,
            payload=payload,
            recorder=recorder,
            graph_state=graph_state,
            api_context=None,
            event_data={
                "kind": "approval",
                "approval_status": "approved",
                "approval_id": str(approval.approval_id),
                "preview_hash": approval.preview_hash,
                "analysis_signature": request.analysis_signature,
                "context_hash": pending.context_hash or run.context_hash,
                "approval_context_hash": (
                    pending.approval_context_hash
                    or pending.context_hash
                    or run.context_hash
                ),
                "target_type": pending.target_type or "",
                "target_id": str(pending.target_id),
                "resumed_by": str(request.actor_id),
                "resume_segment_elapsed_ms": resume_segment_elapsed_ms,
            },
            idempotency_key_hash=idempotency_key_hash,
            resume_payload_hash=resume_payload_hash,
        )
        return AgentResumeResult(
            run_id=run.id,
            trace_id=run.trace_id,
            terminal_state="running",
            resumed=True,
        )

    async def _revalidate_resumed_approval(
        self,
        *,
        run: AgentRun,
        telemetry: AgentTelemetry,
    ) -> ApprovalResumeState:
        if self._approval_resume is None or telemetry.resume_approval is None:
            raise ValueError("approval resume revalidation state is unavailable")
        data = telemetry.resume_approval
        actor = await self._repository.resolve_context_references(
            company_id=run.company_id,
            actor_id=UUID(str(data["resumed_by"])),
        )
        if actor.actor_role != "approver":
            return ApprovalResumeState("stale", "approval_resume_actor_inactive")
        return await self._approval_resume.validate(
            company_id=run.company_id,
            approval_id=UUID(str(data["approval_id"])),
            preview_hash=str(data["preview_hash"]),
            analysis_signature=str(data["analysis_signature"]),
            context_hash=str(
                data.get("approval_context_hash") or data["context_hash"]
            ),
            target_type=str(data["target_type"]),
            target_id=UUID(str(data["target_id"])),
        )

    async def _accept_resume(
        self,
        *,
        run: AgentRun,
        payload: AgentRunPayload,
        recorder: AgentStepRecorder,
        graph_state: GraphState,
        api_context: FrozenContextEnvelope | None,
        event_data: dict[str, object],
        idempotency_key_hash: str,
        resume_payload_hash: str,
    ) -> None:
        telemetry = AgentTelemetry.model_validate(run.telemetry).model_copy(
            update={
                "analysis_signature": graph_state.context.analysis_signature,
                "graph_context": graph_state.context.model_dump(mode="json"),
                "graph_state": graph_state.model_dump(mode="json"),
                "checkpoint": graph_state.checkpoint.model_dump(mode="json"),
                "resume_kind": event_data["kind"],
                "resume_approval": (
                    event_data if event_data["kind"] == "approval" else None
                ),
            }
        )
        run.context_hash = graph_state.context.analysis_signature
        if api_context is not None:
            run.context_envelope = api_context.model_dump(mode="json")
        run.telemetry = telemetry.model_dump(mode="json")
        run.result = payload.model_copy(
            update={
                "message": "The durable interrupt was validated and queued for bounded resume.",
                "missing_fields": [],
                "pending_interrupt": None,
                "approval_requirement": ApprovalRequirement(),
            }
        ).model_dump(mode="json")
        run.stage = f"resumed_{event_data['kind']}"
        run.terminal_state = "running"
        run.completed_at = None
        run.error_code = None
        await self._record_resume_event(
            recorder=recorder,
            pending=payload.pending_interrupt,
            idempotency_key_hash=idempotency_key_hash,
            resume_payload_hash=resume_payload_hash,
            data=event_data,
            commit=False,
        )
        await self._repository.commit()

    async def _record_resume_event(
        self,
        *,
        recorder: AgentStepRecorder,
        pending: PendingInterrupt | None,
        idempotency_key_hash: str,
        resume_payload_hash: str,
        data: dict[str, object],
        commit: bool,
        event_name: EventName = "run.resumed",
        status: str = "completed",
    ) -> None:
        await recorder.record(
            step_type="interrupt",
            event_name=event_name,
            status=status,
            graph_name="orchestrator",
            node_name="interrupt.resume",
            input_summary={
                "interrupt_id": str(pending.interrupt_id) if pending else None,
                "interrupt_sequence": pending.sequence if pending else None,
            },
            data={
                **data,
                "idempotency_key_hash": idempotency_key_hash,
                "resume_payload_hash": resume_payload_hash,
            },
            commit=commit,
        )

    async def _find_resume_attempt(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        idempotency_key_hash: str,
    ) -> str | None:
        cursor = 0
        while True:
            steps = await self._repository.list_steps_after(
                company_id=company_id,
                run_id=run_id,
                after_sequence=cursor,
                limit=MAX_AGENT_RUN_STEP_PAGE_SIZE,
            )
            for step in steps:
                snapshot = step.output_snapshot
                if not isinstance(snapshot, dict) or snapshot.get("event_name") not in {
                    "run.resumed",
                    "run.resume_blocked",
                    "run.resume_resolved",
                }:
                    continue
                data = snapshot.get("data")
                if not isinstance(data, dict):
                    continue
                if data.get("idempotency_key_hash") == idempotency_key_hash:
                    value = data.get("resume_payload_hash")
                    return value if isinstance(value, str) else ""
            if len(steps) < MAX_AGENT_RUN_STEP_PAGE_SIZE:
                break
            cursor = steps[-1].sequence
        return None

    @staticmethod
    def _validate_resume_identity(
        *,
        run: AgentRun,
        pending: PendingInterrupt | None,
        request: AgentResumeRequest,
    ) -> None:
        if run.terminal_state not in {"needs_clarification", "approval_required"}:
            raise AgentResumeError(
                "run_not_resumable",
                "The run is not waiting at a resumable interrupt.",
            )
        if pending is None or pending.status != "pending":
            raise AgentResumeError(
                "interrupt_not_pending",
                "The run has no pending durable interrupt.",
            )
        if pending.interrupt_id != request.interrupt_id:
            raise AgentResumeError(
                "stale_interrupt_id",
                "The interrupt identifier is stale.",
            )
        if pending.sequence != request.interrupt_sequence:
            raise AgentResumeError(
                "stale_interrupt_cursor",
                "The interrupt sequence is stale.",
            )
        if (
            pending.analysis_signature != request.analysis_signature
            or run.context_hash != (pending.context_hash or pending.analysis_signature)
        ):
            raise AgentResumeError(
                "resume_analysis_signature_mismatch",
                "The analysis signature no longer matches the pending run.",
            )

    async def _execute_loaded(
        self,
        *,
        run: AgentRun,
        request: AgentQueryRequest,
        recorder: AgentStepRecorder,
        budget: RuntimeBudgetCounter,
        started_clock: float,
        claim_id: UUID,
        include_entity_resolution: bool,
    ) -> None:
        try:
            preflight_policy(request.query)
        except PolicyBlockedError as error:
            await recorder.record(
                step_type="policy",
                event_name="validation.warning",
                status="blocked",
                graph_name="orchestrator",
                node_name="policy.preflight",
                error_code=error.code,
                data={"code": error.code},
            )
            await self._stop(
                run=run,
                recorder=recorder,
                terminal_state="policy_blocked",
                code=error.code,
                message="The request was blocked by the bounded agent policy.",
                budget=budget,
                started_clock=started_clock,
            )
            return

        await recorder.record(
            step_type="policy",
            event_name="node.completed",
            status="completed",
            graph_name="orchestrator",
            node_name="policy.preflight",
            data={"policy_version": "agent-policy.v1", "allowed": True},
        )
        try:
            model = self._model_factory()
        except AIConfigurationError as error:
            await recorder.record(
                step_type="provider",
                event_name="provider.failed",
                status="failed",
                graph_name="orchestrator",
                node_name="planner.provider",
                error_code=error.code,
                data={"code": error.code, "provider": "none"},
            )
            await self._stop(
                run=run,
                recorder=recorder,
                terminal_state="provider_unavailable",
                code=error.code,
                message="The configured model provider is unavailable.",
                budget=budget,
                started_clock=started_clock,
                provider_status="unavailable",
            )
            return

        async def observe_provider(event: ProviderLifecycleEvent) -> None:
            telemetry = AgentTelemetry.model_validate(run.telemetry)
            if event.event_name == "provider.started":
                run.model_calls += 1
                run.api_calls += 1
            elif event.event_name == "provider.completed":
                run.input_tokens += event.input_tokens
                run.output_tokens += event.output_tokens
                run.latency_ms += event.latency_ms
            telemetry = telemetry.model_copy(
                update={
                    "provider": event.provider,
                    "provider_status": (
                        "completed"
                        if event.event_name == "provider.completed"
                        else "unavailable"
                        if event.event_name == "provider.failed"
                        else "configured"
                    ),
                    "model_id": event.model_id,
                    "model_calls": run.model_calls,
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "cached_input_tokens": (
                        telemetry.cached_input_tokens + event.cached_input_tokens
                        if event.event_name == "provider.completed"
                        else telemetry.cached_input_tokens
                    ),
                    "api_calls": run.api_calls,
                    "retry_count": run.retry_count,
                    "repairs": budget.repairs,
                }
            )
            run.telemetry = telemetry.model_dump(mode="json")
            await recorder.record(
                step_type="provider",
                event_name=event.event_name,
                status=(
                    "completed"
                    if event.event_name == "provider.completed"
                    else "failed"
                    if event.event_name == "provider.failed"
                    else "running"
                ),
                graph_name="orchestrator",
                node_name=f"planner.provider.attempt_{event.attempt}",
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                retry_count=0,
                latency_ms=event.latency_ms,
                error_code=event.error_code,
                data={
                    "provider": event.provider,
                    "model_id": event.model_id,
                    "attempt": event.attempt,
                    "repair_attempt": event.attempt > 1,
                    "provider_request_id": event.provider_request_id,
                    "finish_reason": event.finish_reason,
                    "cached_input_tokens": event.cached_input_tokens,
                    "code": event.error_code,
                },
            )
            if event.event_name == "provider.completed" and event.cached_input_tokens > 0:
                await recorder.record(
                    step_type="provider",
                    event_name="provider.cache_hit",
                    status="completed",
                    graph_name="orchestrator",
                    node_name=f"planner.provider.attempt_{event.attempt}",
                    input_tokens=event.cached_input_tokens,
                    data={
                        "provider": event.provider,
                        "model_id": event.model_id,
                        "attempt": event.attempt,
                        "cached_input_tokens": event.cached_input_tokens,
                        "cache_hit": True,
                    },
                )

        runner = StructuredModelRunner(model, observer=observe_provider)
        try:
            planned = await runner.generate(
                PlannerSelection,
                stage="intent_planning",
                system_instruction=planning_system_instruction(),
                user_content=await self._planner_content(request.query, request.context),
                budget=budget,
            )
        except (AIProviderError, AIConfigurationError) as error:
            await self._stop(
                run=run,
                recorder=recorder,
                terminal_state="provider_unavailable",
                code=getattr(error, "code", "ai_provider_unavailable"),
                message="The selected model provider could not complete structured planning.",
                budget=budget,
                started_clock=started_clock,
                provider_status="unavailable",
            )
            return
        except StructuredOutputValidationError as error:
            await recorder.record(
                step_type="validation",
                event_name="validation.warning",
                status="failed",
                graph_name="orchestrator",
                node_name="planner.validate",
                error_code=error.code,
                data={"code": error.code, "repair_used": budget.repairs == 1},
            )
            await self._stop(
                run=run,
                recorder=recorder,
                terminal_state="validation_failed",
                code=error.code,
                message="The model response did not match the strict execution-plan contract.",
                budget=budget,
                started_clock=started_clock,
            )
            return
        except RuntimeBudgetExhausted as error:
            await self._stop(
                run=run,
                recorder=recorder,
                terminal_state="budget_exhausted",
                code=f"{error.dimension}_budget_exhausted",
                message="The agent stopped before exceeding its planning budget.",
                budget=budget,
                started_clock=started_clock,
            )
            return

        selection = planned.value
        telemetry = AgentTelemetry.model_validate(run.telemetry).model_copy(
            update={
                "planning_selection": selection.model_dump(mode="json"),
                "model_calls": budget.model_calls,
                "retry_count": run.retry_count,
                "repairs": budget.repairs,
            }
        )
        run.telemetry = telemetry.model_dump(mode="json")
        await recorder.record(
            step_type="planner",
            event_name="node.completed",
            status="completed",
            graph_name="orchestrator",
            node_name="planner.structured",
            data={
                "disposition": selection.disposition,
                "modules": list(selection.modules),
                "reason_code": selection.reason_code,
                "repair_used": planned.repaired,
            },
        )

        if selection.disposition == "policy_blocked":
            await self._stop(
                run=run,
                recorder=recorder,
                terminal_state="policy_blocked",
                code=selection.reason_code,
                message="The structured planner identified a prohibited action.",
                budget=budget,
                started_clock=started_clock,
            )
            return
        if selection.disposition == "unsupported":
            await self._stop(
                run=run,
                recorder=recorder,
                terminal_state="unsupported",
                code=selection.reason_code,
                message="The request is outside the four bounded CarbonMesh modules.",
                budget=budget,
                started_clock=started_clock,
            )
            return
        if selection.disposition == "clarify":
            await self._interrupt_for_clarification(
                run=run,
                recorder=recorder,
                fields=list(selection.clarification_fields),
                code=selection.reason_code,
                budget=budget,
                started_clock=started_clock,
            )
            return

        try:
            plan = build_execution_plan(
                selection,
                include_entity_resolution=include_entity_resolution,
                fresh_inputs=request.context.fresh_inputs,
            )
        except ValueError:
            await self._stop(
                run=run,
                recorder=recorder,
                terminal_state="validation_failed",
                code="execution_plan_policy_failed",
                message="The structured plan failed deterministic graph policy validation.",
                budget=budget,
                started_clock=started_clock,
            )
            return

        budget.limits = budget_limits_for(plan.profile)
        try:
            budget.check_deadline()
        except RuntimeBudgetExhausted as error:
            await self._stop(
                run=run,
                recorder=recorder,
                terminal_state="budget_exhausted",
                code=f"{error.dimension}_budget_exhausted",
                message="The agent stopped before exceeding its selected runtime budget.",
                budget=budget,
                started_clock=started_clock,
            )
            return

        api_context, graph_context = freeze_runtime_context(
            request_context=request.context,
            actor_role=_frozen_context_role(run.context_envelope),
            query=request.query,
            plan=plan,
        )
        missing_fields = _required_context_fields(plan, request.context)
        run.workflow = workflow_for_modules(plan.modules)
        run.context_envelope = api_context.model_dump(mode="json")
        run.context_hash = graph_context.analysis_signature
        run.plan = plan.model_dump(mode="json")
        run.stage = "planned"
        planning_elapsed_ms = max(run.latency_ms, budget.elapsed_ms)
        run.latency_ms = planning_elapsed_ms
        telemetry = AgentTelemetry.model_validate(run.telemetry).model_copy(
            update={
                "analysis_signature": graph_context.analysis_signature,
                "graph_context": graph_context.model_dump(mode="json"),
                "elapsed_ms": planning_elapsed_ms,
                "stage_timings": [
                    StageTelemetry(stage="total", elapsed_ms=planning_elapsed_ms)
                ],
            }
        )
        run.telemetry = telemetry.model_dump(mode="json")
        await self._repository.commit()
        await recorder.record(
            step_type="policy",
            event_name="node.completed",
            status="completed",
            graph_name="orchestrator",
            node_name="plan.policy",
            data={
                "plan_hash": plan.plan_hash,
                "profile": plan.profile,
                "modules": list(plan.modules),
                "max_model_calls": plan.expected_model_calls,
                "max_tool_calls": budget_limits_for(plan.profile).max_tool_calls,
            },
        )
        if missing_fields:
            await self._interrupt_for_clarification(
                run=run,
                recorder=recorder,
                fields=missing_fields,
                code="context_incomplete",
                budget=budget,
                started_clock=started_clock,
            )
            return

        graph_budget = BudgetState.for_profile(plan.profile).model_copy(
            update={
                "model_calls": budget.model_calls,
                "elapsed_ms": planning_elapsed_ms,
                "repairs_by_stage": (
                    (StageRepairCount(stage_id="intent_planning", count=budget.repairs),)
                    if budget.repairs
                    else ()
                ),
            }
        )
        initial = AgentGraphState.initial(
            run_id=run.id,
            context=graph_context,
            plan=plan,
            budget=graph_budget,
        )
        persistence_lock = asyncio.Lock()
        invoker = _RuntimeBudgetInvoker(
            RegistryGraphToolInvoker(
                self._tool_registry_factory(),
                recorder=recorder,
                persistence_lock=persistence_lock,
            ),
            budget,
        )
        graph = compile_agent_graphs(invoker).orchestrator
        final = await self._run_graph_durably(
            run=run,
            recorder=recorder,
            graph=graph,
            initial=initial,
            claim_id=claim_id,
            runtime_budget=budget,
            persistence_lock=persistence_lock,
        )
        await self._finalize_graph(
            run=run,
            recorder=recorder,
            state=final,
            budget=budget,
            started_clock=started_clock,
        )

    async def _run_graph_durably(
        self,
        *,
        run: AgentRun,
        recorder: AgentStepRecorder,
        graph: Any,
        initial: GraphState,
        claim_id: UUID,
        runtime_budget: RuntimeBudgetCounter,
        persistence_lock: asyncio.Lock,
    ) -> GraphState:
        """Persist each emitted node state before allowing the next node to run."""

        initial = _with_runtime_elapsed(initial, runtime_budget)
        final = initial
        cursor = initial.checkpoint.last_transition_sequence
        async with persistence_lock:
            await self._persist_graph_checkpoint(
                run=run,
                state=initial,
                claim_id=claim_id,
            )
        # A failed/cancelled checkpoint consumer must close the stream and await
        # graph children before outer recovery or session cleanup can run.
        async with aclosing(
            graph.astream(initial, stream_mode="values", subgraphs=True)
        ) as stream:
            async for emitted in stream:
                # Nested specialist emissions arrive as (namespace, state).
                raw_state = emitted[1] if isinstance(emitted, tuple) else emitted
                current = (
                    raw_state
                    if isinstance(raw_state, GraphState)
                    else GraphState.model_validate(raw_state)
                )
                current = _with_runtime_elapsed(current, runtime_budget)
                if current.checkpoint.last_transition_sequence <= cursor:
                    final = current
                    continue
                async with persistence_lock:
                    await self._persist_graph_transitions(
                        recorder,
                        current,
                        after_sequence=cursor,
                    )
                    await self._persist_graph_checkpoint(
                        run=run,
                        state=current,
                        claim_id=claim_id,
                    )
                cursor = current.checkpoint.last_transition_sequence
                final = current
        return final

    async def _persist_graph_checkpoint(
        self,
        *,
        run: AgentRun,
        state: GraphState,
        claim_id: UUID,
    ) -> None:
        await self._renew_execution_claim(run=run, claim_id=claim_id)
        telemetry = AgentTelemetry.model_validate(run.telemetry).model_copy(
            update={
                "graph_context": state.context.model_dump(mode="json"),
                "graph_state": state.model_dump(mode="json"),
                "checkpoint": state.checkpoint.model_dump(mode="json"),
                "tool_calls": state.budget.tool_calls,
            }
        )
        run.stage = "graph"
        run.tool_calls = max(run.tool_calls, state.budget.tool_calls)
        run.telemetry = telemetry.model_dump(mode="json")
        await self._repository.commit()

    async def _persist_graph_transitions(
        self,
        recorder: AgentStepRecorder,
        state: GraphState,
        *,
        after_sequence: int = 0,
    ) -> None:
        for transition in state.transitions:
            if transition.sequence <= after_sequence:
                continue
            if transition.kind == "tool":
                continue
            if transition.status == "started":
                event_name = "node.started"
                status = "running"
            elif transition.status in {"completed", "skipped"}:
                event_name = "node.completed"
                status = "completed" if transition.status == "completed" else "skipped"
            else:
                event_name = "node.failed"
                status = "blocked" if transition.status in {"blocked", "interrupted"} else "failed"
            await recorder.record(
                step_type=("node" if transition.kind == "graph" else transition.kind),
                event_name=event_name,
                status=status,
                graph_name=transition.graph_name,
                node_name=transition.node_name,
                tool_name=transition.tool_name,
                error_code=transition.code,
                data={
                    "transition_sequence": transition.sequence,
                    "module": transition.module,
                    "terminal_state": transition.terminal_state,
                    "code": transition.code,
                },
                commit=False,
            )

    async def _finalize_graph(
        self,
        *,
        run: AgentRun,
        recorder: AgentStepRecorder,
        state: GraphState,
        budget: RuntimeBudgetCounter,
        started_clock: float,
    ) -> None:
        facts = [
            AgentFact(
                fact_id=fact.fact_id,
                metric_key=fact.metric_key,
                display_value=fact.display_value,
                ledger_event_id=fact.ledger_event_id,
            )
            for fact in state.facts
        ]
        unsupported_items = [item.reason for item in state.unsupported_items]
        if state.status == "interrupted" and state.pending_interrupt is not None:
            if isinstance(state.pending_interrupt, ApprovalInterrupt):
                step = await recorder.record(
                    step_type="interrupt",
                    event_name="approval.required",
                    status="blocked",
                    graph_name="orchestrator",
                    node_name="interrupt.approval",
                    data={
                        "approval_id": str(state.pending_interrupt.approval_id),
                        "target_type": state.pending_interrupt.target_type,
                        "target_id": str(state.pending_interrupt.target_id),
                        "preview_hash": state.pending_interrupt.preview_hash,
                        "analysis_signature": state.pending_interrupt.analysis_signature,
                        "context_hash": state.pending_interrupt.context_hash,
                        "approval_context_hash": (
                            state.pending_interrupt.approval_context_hash
                        ),
                    },
                    commit=False,
                )
                pending = PendingInterrupt(
                    interrupt_id=uuid4(),
                    sequence=step.sequence,
                    kind="approval",
                    analysis_signature=state.pending_interrupt.analysis_signature,
                    context_hash=state.pending_interrupt.context_hash,
                    approval_context_hash=(
                        state.pending_interrupt.approval_context_hash
                    ),
                    approval_id=state.pending_interrupt.approval_id,
                    target_type=state.pending_interrupt.target_type,
                    target_id=state.pending_interrupt.target_id,
                    preview_hash=state.pending_interrupt.preview_hash,
                    expires_at=state.pending_interrupt.expires_at,
                )
                approval = ApprovalRequirement(
                    required=True,
                    approval_id=state.pending_interrupt.approval_id,
                    recommendation_id=state.pending_interrupt.target_id,
                    preview_hash=state.pending_interrupt.preview_hash,
                )
            else:
                step = await recorder.record(
                    step_type="interrupt",
                    event_name="clarification.required",
                    status="blocked",
                    graph_name="orchestrator",
                    node_name="interrupt.clarification",
                    data={
                        "required_fields": list(state.pending_interrupt.required_fields),
                        "code": state.pending_interrupt.code,
                    },
                    commit=False,
                )
                pending = PendingInterrupt(
                    interrupt_id=uuid4(),
                    sequence=step.sequence,
                    kind="clarification",
                    analysis_signature=run.context_hash or state.context.analysis_signature,
                    missing_fields=list(state.pending_interrupt.required_fields),
                )
                approval = ApprovalRequirement()
            payload = AgentRunPayload(
                message="The agent run is paused at a durable human interrupt.",
                facts=facts,
                judgments=[_judgment(run.workflow)],
                approval_requirement=approval,
                unsupported_items=unsupported_items,
                pending_interrupt=pending,
            )
            await self._persist_terminal(
                run=run,
                recorder=recorder,
                terminal_state=state.terminal_state or "needs_clarification",
                code=state.error_code,
                payload=payload,
                graph_state=state,
                budget=budget,
                started_clock=started_clock,
                final_event_name="run.stopped",
            )
            return

        terminal_state = state.terminal_state or "validation_failed"
        if terminal_state == "success" and not facts:
            terminal_state = "validation_failed"
            state = GraphState.model_validate(
                state.model_copy(
                    update={
                        "status": "stopped",
                        "terminal_state": "validation_failed",
                        "error_code": "verified_facts_missing",
                    }
                ).model_dump(mode="python")
            )
        success = terminal_state == "success"
        payload = AgentRunPayload(
            message=(
                _fact_bound_summary(facts)
                if success
                else "The bounded LangGraph workflow stopped without fabricating a result."
            ),
            facts=facts,
            judgments=[_judgment(run.workflow)],
            unsupported_reason=(unsupported_items[0] if unsupported_items else None),
            unsupported_items=unsupported_items,
        )
        await self._persist_terminal(
            run=run,
            recorder=recorder,
            terminal_state=terminal_state,
            code=state.error_code,
            payload=payload,
            graph_state=state,
            budget=budget,
            started_clock=started_clock,
            final_event_name="run.completed" if success else "run.stopped",
        )

    async def _interrupt_for_clarification(
        self,
        *,
        run: AgentRun,
        recorder: AgentStepRecorder,
        fields: list[str],
        code: str,
        budget: RuntimeBudgetCounter,
        started_clock: float,
    ) -> None:
        interrupt_id = uuid4()
        step = await recorder.record(
            step_type="interrupt",
            event_name="clarification.required",
            status="blocked",
            graph_name="orchestrator",
            node_name="interrupt.clarification",
            error_code=code,
            data={
                "interrupt_id": str(interrupt_id),
                "required_fields": fields,
                "analysis_signature": run.context_hash,
                "code": code,
            },
            commit=False,
        )
        pending = PendingInterrupt(
            interrupt_id=interrupt_id,
            sequence=step.sequence,
            kind="clarification",
            analysis_signature=run.context_hash,
            missing_fields=fields,
        )
        payload = AgentRunPayload(
            message="Provide the missing frozen-context fields and resume this run.",
            judgments=[_judgment(run.workflow)],
            missing_fields=fields,
            pending_interrupt=pending,
        )
        await self._persist_terminal(
            run=run,
            recorder=recorder,
            terminal_state="needs_clarification",
            code=code,
            payload=payload,
            graph_state=None,
            budget=budget,
            started_clock=started_clock,
            final_event_name="run.stopped",
        )

    async def _stop(
        self,
        *,
        run: AgentRun,
        recorder: AgentStepRecorder,
        terminal_state: str,
        code: str,
        message: str,
        budget: RuntimeBudgetCounter,
        started_clock: float,
        provider_status: str | None = None,
    ) -> None:
        payload = AgentRunPayload(
            message=message,
            judgments=[_judgment(run.workflow)],
            unsupported_reason=message if terminal_state == "unsupported" else None,
        )
        await self._persist_terminal(
            run=run,
            recorder=recorder,
            terminal_state=terminal_state,
            code=code,
            payload=payload,
            graph_state=None,
            budget=budget,
            started_clock=started_clock,
            final_event_name="run.stopped",
            provider_status=provider_status,
        )

    async def _persist_terminal(
        self,
        *,
        run: AgentRun,
        recorder: AgentStepRecorder,
        terminal_state: str,
        code: str | None,
        payload: AgentRunPayload,
        graph_state: GraphState | None,
        budget: RuntimeBudgetCounter,
        started_clock: float,
        final_event_name: str,
        provider_status: str | None = None,
        clear_checkpoint: bool = False,
    ) -> None:
        elapsed_ms = max(0, round((perf_counter() - started_clock) * 1000))
        budget_elapsed_ms = budget.elapsed_ms
        graph_elapsed_ms = graph_state.budget.elapsed_ms if graph_state is not None else 0
        cumulative_elapsed_ms = _terminal_elapsed_ms(
            persisted_elapsed_ms=max(0, run.latency_ms),
            segment_elapsed_ms=elapsed_ms,
            budget_elapsed_ms=budget_elapsed_ms,
            graph_elapsed_ms=graph_elapsed_ms,
            has_graph_state=graph_state is not None,
        )
        if graph_state is not None:
            graph_state = _with_elapsed_ms(graph_state, cumulative_elapsed_ms)
        run.stage = (
            "completed"
            if terminal_state == "success"
            else "interrupted"
            if terminal_state in {"needs_clarification", "approval_required"}
            else "stopped"
        )
        run.terminal_state = terminal_state
        run.result = payload.model_dump(mode="json")
        run.model_calls = max(run.model_calls, budget.model_calls)
        if graph_state is not None:
            run.tool_calls = max(run.tool_calls, graph_state.budget.tool_calls)
        run.latency_ms = cumulative_elapsed_ms
        run.completed_at = datetime.now(UTC)
        run.error_code = None if terminal_state == "success" else code or terminal_state
        telemetry = AgentTelemetry.model_validate(run.telemetry)
        clear_resumed_checkpoint = graph_state is None and (
            clear_checkpoint or telemetry.resume_kind is not None
        )
        rows_processed = (
            sum(record.rows_processed for record in graph_state.tool_records)
            if graph_state is not None
            else telemetry.rows_processed
        )
        evidence_chunks = (
            sum(record.evidence_chunks_retrieved for record in graph_state.tool_records)
            if graph_state is not None
            else telemetry.evidence_chunks_retrieved
        )
        cache_hits = (
            sum(record.cache_hit for record in graph_state.tool_records)
            if graph_state is not None
            else telemetry.cache_hits
        )
        external = take_external_usage()
        run.api_calls += external.api_calls
        run.retry_count += external.retry_count
        external_cache_hits = telemetry.external_cache_hits + external.cache_hits
        cache_hits = max(cache_hits, external_cache_hits)
        settings = get_settings()
        if telemetry.sustainability_assumptions:
            saved = telemetry.sustainability_assumptions
            settings = settings.model_copy(update={
                "ai_energy_wh_per_1k_tokens": Decimal(saved["energy_wh_per_1k_tokens"]),
                "ai_grid_intensity_gco2e_per_kwh": Decimal(saved["grid_intensity_gco2e_per_kwh"]),
            })
        energy_wh, co2e_g = estimate_sustainability_proxy(
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            settings=settings,
        )
        run.estimated_energy_wh = energy_wh
        run.estimated_co2e_g = co2e_g
        telemetry = telemetry.model_copy(
            update={
                "analysis_signature": run.context_hash or telemetry.analysis_signature,
                "provider_status": provider_status or telemetry.provider_status,
                "model_calls": run.model_calls,
                "tool_calls": run.tool_calls,
                "retry_count": run.retry_count,
                "repairs": max(telemetry.repairs, budget.repairs),
                "api_calls": run.api_calls,
                "cache_hits": cache_hits,
                "external_cache_hits": external_cache_hits,
                "rows_processed": rows_processed,
                "evidence_chunks_retrieved": evidence_chunks,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "elapsed_ms": run.latency_ms,
                "estimated_energy_wh": energy_wh,
                "estimated_co2e_g": co2e_g,
                "sustainability_method": SUSTAINABILITY_PROXY_METHOD,
                "sustainability_assumptions": telemetry.sustainability_assumptions or {
                    "method": SUSTAINABILITY_PROXY_METHOD,
                    "version": SUSTAINABILITY_PROXY_VERSION,
                    "energy_wh_per_1k_tokens": str(settings.ai_energy_wh_per_1k_tokens),
                    "grid_intensity_gco2e_per_kwh": str(settings.ai_grid_intensity_gco2e_per_kwh),
                },
                "checkpoint": (
                    graph_state.checkpoint.model_dump(mode="json")
                    if graph_state is not None
                    else None
                    if clear_resumed_checkpoint
                    else telemetry.checkpoint
                ),
                "graph_state": (
                    graph_state.model_dump(mode="json")
                    if graph_state is not None
                    else None
                    if clear_resumed_checkpoint
                    else telemetry.graph_state
                ),
                "resume_kind": None,
                "resume_approval": None,
                "stage_timings": [
                    StageTelemetry(stage="total", elapsed_ms=run.latency_ms)
                ],
            }
        )
        run.telemetry = telemetry.model_dump(mode="json")
        await recorder.record(
            step_type="finalize",
            event_name=final_event_name,
            status="completed" if terminal_state == "success" else "blocked",
            graph_name="orchestrator",
            node_name="orchestrator.finalize",
            error_code=run.error_code,
            data={
                "terminal_state": terminal_state,
                "code": run.error_code,
                "model_calls": run.model_calls,
                "tool_calls": run.tool_calls,
                "repairs": max(telemetry.repairs, budget.repairs),
                "latency_ms": run.latency_ms,
            },
            commit=False,
        )
        await self._repository.commit()

    async def get(self, *, company_id: UUID, run_id: UUID) -> AgentRunResult:
        run = await self._repository.get(company_id=company_id, run_id=run_id)
        if run is None:
            raise GraphAgentRunNotFoundError
        payload = AgentRunPayload.model_validate(run.result or {"message": "Run is in progress."})
        return AgentRunResult(
            run_id=run.id,
            trace_id=run.trace_id,
            workflow=run.workflow,
            stage=run.stage,
            terminal_state=run.terminal_state,
            context=run.context_envelope,
            plan=run.plan,
            facts=payload.facts,
            judgments=payload.judgments,
            telemetry=run.telemetry,
            approval_requirement=payload.approval_requirement,
            recommendation=payload.recommendation,
            pending_interrupt=payload.pending_interrupt,
            message=payload.message,
            missing_fields=payload.missing_fields,
            unsupported_reason=payload.unsupported_reason,
            unsupported_items=payload.unsupported_items,
            error_code=run.error_code,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )


def _fact_bound_summary(facts: list[AgentFact]) -> str:
    """Render only domain-formatted fact values, never model-authored numbers."""
    labels = {
        "emissions.scope2.location_based": "Location-based Scope 2 emissions",
        "emissions.scope3.category1": "Purchased-material emissions",
        "procurement.projected_avoided_emissions": "Projected procurement emissions avoided",
        "dispatch.avoided_emissions": "Advisory dispatch emissions avoided",
    }
    statements = [f"{labels[fact.metric_key]}: {fact.display_value} [fact:{fact.fact_id}]."
                  for fact in facts if fact.metric_key in labels]
    return "The workflow completed with verified facts. " + " ".join(dict.fromkeys(statements))


def _load_execution_plan(value: dict[str, object]) -> ExecutionPlan:
    return ExecutionPlan.model_validate_json(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)
    )


def _load_graph_state(value: dict[str, object]) -> GraphState:
    return GraphState.model_validate_json(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)
    )


def _load_graph_context(value: dict[str, object]) -> ContextEnvelope:
    return ContextEnvelope.model_validate_json(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)
    )


def _resume_execution_budget(
    *,
    state: GraphState,
    persisted_elapsed_ms: int,
    model_calls: int,
    repairs: int,
    approval_resume: bool,
    resume_segment_elapsed_ms: int,
) -> RuntimeBudgetCounter:
    """Restore cumulative limits without resetting them at an interrupt."""

    budget = new_budget(state.plan.profile)
    budget.model_calls = model_calls
    budget.tool_calls = state.budget.tool_calls
    budget.repairs = repairs
    budget.constrain_latency(
        cumulative_elapsed_ms=max(persisted_elapsed_ms, state.budget.elapsed_ms),
        segment_limit_ms=(
            max(
                0,
                budget_limits_for("resume").target_latency_ms
                - resume_segment_elapsed_ms,
            )
            if approval_resume
            else None
        ),
    )
    return budget


def _terminal_elapsed_ms(
    *,
    persisted_elapsed_ms: int,
    segment_elapsed_ms: int,
    budget_elapsed_ms: int,
    graph_elapsed_ms: int,
    has_graph_state: bool,
) -> int:
    """Combine cumulative clocks without counting planning time twice."""

    if budget_elapsed_ms >= persisted_elapsed_ms or has_graph_state:
        return max(persisted_elapsed_ms, budget_elapsed_ms, graph_elapsed_ms)
    return persisted_elapsed_ms + segment_elapsed_ms


def _with_elapsed_ms(state: GraphState, elapsed_ms: int) -> GraphState:
    if elapsed_ms <= state.budget.elapsed_ms:
        return state
    return GraphState.model_validate(
        state.model_copy(
            update={
                "budget": state.budget.model_copy(update={"elapsed_ms": elapsed_ms})
            }
        ).model_dump(mode="python")
    )


def _with_runtime_elapsed(
    state: GraphState,
    budget: RuntimeBudgetCounter,
) -> GraphState:
    return _with_elapsed_ms(state, budget.elapsed_ms)


def _prepare_resumed_graph_state(
    state: GraphState,
    *,
    context: ContextEnvelope | None,
    resolve_approval: bool,
) -> GraphState:
    selected_context = context or state.context
    completed_nodes = state.checkpoint.completed_nodes
    if resolve_approval:
        interrupted = next(
            (
                transition
                for transition in reversed(state.transitions)
                if transition.status == "interrupted" and transition.tool_name is not None
            ),
            None,
        )
        if interrupted is None:
            raise ValueError("approval checkpoint has no interrupted tool node")
        if interrupted.node_name not in completed_nodes:
            completed_nodes += (interrupted.node_name,)
    checkpoint = state.checkpoint.model_copy(
        update={
            "revision": state.checkpoint.revision + 1,
            "context_hash": selected_context.analysis_signature,
            "completed_nodes": completed_nodes,
            "pending_interrupt": None,
        }
    )
    resumed = state.model_copy(
        update={
            "context": selected_context,
            "checkpoint": checkpoint,
            "status": "running",
            "terminal_state": None,
            "pending_interrupt": None,
            "error_code": None,
        }
    )
    return GraphState.model_validate(resumed.model_dump(mode="python"))


def _resume_request_hashes(request: AgentResumeRequest) -> tuple[str, str]:
    key_hash = hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()
    payload = request.model_dump(mode="json")
    payload.pop("idempotency_key", None)
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return key_hash, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _preserves_frozen_context(
    frozen: FrozenContextEnvelope,
    clarified: AgentContextRequest,
) -> bool:
    scalar_fields = (
        "company_id",
        "grid_source_mode",
        "fresh_inputs",
        "actor_id",
        "site_id",
        "reporting_period_id",
        "carbon_measurement_id",
        "current_product_id",
        "method_definition_id",
        "standard_id",
        "disclosure_draft_id",
        "procurement_scenario_id",
        "flexible_load_id",
        "dispatch_scenario_id",
        "forecast_id",
        "policy_definition_id",
    )
    for field_name in scalar_fields:
        old_value = getattr(frozen, field_name)
        new_value = getattr(clarified, field_name)
        if old_value is not None and new_value is not None and new_value != old_value:
            return False
    for field_name in (
        "activity_record_ids",
        "requirement_ids",
        "evidence_item_ids",
        "metric_keys",
        "material_scope",
        "supplier_product_ids",
    ):
        old_values = getattr(frozen, field_name)
        new_values = getattr(clarified, field_name)
        if old_values and new_values and new_values != old_values:
            return False
    frozen_constraints = frozen.constraints.model_dump(mode="python")
    clarified_constraints = clarified.constraints.model_dump(mode="python")
    procurement_preserved = all(
        value is None
        or clarified_constraints.get(key) is None
        or clarified_constraints.get(key) == value
        for key, value in frozen_constraints.items()
    )
    frozen_dispatch = frozen.dispatch_constraints.model_dump(mode="python")
    clarified_dispatch = clarified.dispatch_constraints.model_dump(mode="python")
    dispatch_preserved = all(
        value in (None, [])
        or clarified_dispatch.get(key) in (None, [])
        or clarified_dispatch.get(key) == value
        for key, value in frozen_dispatch.items()
    )
    return procurement_preserved and dispatch_preserved


def _merge_frozen_context(
    frozen: FrozenContextEnvelope,
    clarified: AgentContextRequest,
) -> AgentContextRequest:
    updates: dict[str, object] = {}
    for field_name in (
        "site_id",
        "fresh_inputs",
        "reporting_period_id",
        "carbon_measurement_id",
        "current_product_id",
        "method_definition_id",
        "standard_id",
        "disclosure_draft_id",
        "procurement_scenario_id",
        "flexible_load_id",
        "dispatch_scenario_id",
        "forecast_id",
        "policy_definition_id",
    ):
        if getattr(clarified, field_name) is None:
            updates[field_name] = getattr(frozen, field_name)
    for field_name in (
        "activity_record_ids",
        "requirement_ids",
        "evidence_item_ids",
        "metric_keys",
        "material_scope",
        "supplier_product_ids",
    ):
        if not getattr(clarified, field_name):
            updates[field_name] = list(getattr(frozen, field_name))
    constraint_updates = {
        key: old_value
        for key, old_value in frozen.constraints.model_dump(mode="python").items()
        if getattr(clarified.constraints, key) is None
    }
    updates["constraints"] = clarified.constraints.model_copy(update=constraint_updates)
    dispatch_updates = {
        key: old_value
        for key, old_value in frozen.dispatch_constraints.model_dump(mode="python").items()
        if getattr(clarified.dispatch_constraints, key) in (None, [])
    }
    updates["dispatch_constraints"] = clarified.dispatch_constraints.model_copy(
        update=dispatch_updates
    )
    return clarified.model_copy(update=updates)


def _frozen_context_role(context: dict[str, object]) -> str:
    role = context.get("actor_role")
    return role if isinstance(role, str) else "system"


def _available_context_fields(context: AgentContextRequest) -> list[str]:
    payload = context.model_dump(mode="python")
    return [
        key
        for key, value in payload.items()
        if value not in (None, [], {})
    ]


def _reference_scope(context: AgentContextRequest) -> dict[str, object]:
    return {
        "site_id": context.site_id,
        "reporting_period_id": context.reporting_period_id,
        "carbon_measurement_id": context.carbon_measurement_id,
        "current_product_id": context.current_product_id,
        "method_definition_id": context.method_definition_id,
        "activity_record_ids": context.activity_record_ids,
        "supplier_product_ids": context.supplier_product_ids,
        "standard_id": context.standard_id,
        "disclosure_draft_id": context.disclosure_draft_id,
        "requirement_ids": context.requirement_ids,
        "evidence_item_ids": context.evidence_item_ids,
        "procurement_scenario_id": context.procurement_scenario_id,
        "flexible_load_id": context.flexible_load_id,
        "dispatch_scenario_id": context.dispatch_scenario_id,
        "forecast_id": context.forecast_id,
        "policy_definition_id": context.policy_definition_id,
    }


def _required_context_fields(
    plan: ExecutionPlan,
    context: AgentContextRequest,
) -> list[str]:
    missing: list[str] = []
    if context.site_id is None:
        missing.append("context.site_id")
    if context.reporting_period_id is None:
        missing.append("context.reporting_period_id")
    if "measurement" in plan.modules and (
        context.fresh_inputs is None
        and
        context.carbon_measurement_id is None
        and not context.material_scope
        and not context.activity_record_ids
    ):
        missing.append(
            "context.carbon_measurement_id_or_material_scope_or_activity_record_ids"
        )
    if "assurance" in plan.modules:
        if context.standard_id is None:
            missing.append("context.standard_id")
        if context.disclosure_draft_id is None and context.fresh_inputs is None:
            missing.append("context.disclosure_draft_id")
    if "procurement" in plan.modules:
        if context.procurement_scenario_id is None and context.fresh_inputs is None:
            missing.append("context.procurement_scenario_id")
        if not context.material_scope:
            missing.append("context.material_scope")
        constraints = context.constraints
        if constraints.max_cost_increase_pct is None:
            missing.append("context.constraints.max_cost_increase_pct")
        if constraints.max_lead_time_days is None:
            missing.append("context.constraints.max_lead_time_days")
        if constraints.minimum_circularity_score is None:
            missing.append("context.constraints.minimum_circularity_score")
    if "dispatch" in plan.modules:
        if context.flexible_load_id is None:
            missing.append("context.flexible_load_id")
        if context.dispatch_scenario_id is None and context.fresh_inputs is None:
            missing.append("context.dispatch_scenario_id")
        if context.dispatch_constraints.window_start is None:
            missing.append("context.dispatch_constraints.window_start")
    fresh = context.fresh_inputs
    if fresh is not None:
        if "measurement" in plan.modules and not fresh.measurements:
            missing.append("context.fresh_inputs.measurements")
        if "procurement" in plan.modules and context.procurement_scenario_id is None:
            for key, value in {"context.current_product_id": context.current_product_id,
                               "context.fresh_inputs.procurement_quantity": fresh.procurement_quantity,
                               "context.fresh_inputs.procurement_method_id": fresh.procurement_method_id}.items():
                if value is None:
                    missing.append(key)
        if "dispatch" in plan.modules and context.dispatch_scenario_id is None:
            for key, value in {"context.fresh_inputs.dispatch_method_id": fresh.dispatch_method_id,
                               "context.fresh_inputs.dispatch_baseline_start": fresh.dispatch_baseline_start,
                               "context.dispatch_constraints.window_end": context.dispatch_constraints.window_end,
                               "context.dispatch_constraints.max_delay_minutes": context.dispatch_constraints.max_delay_minutes}.items():
                if value is None:
                    missing.append(key)
    return missing


def _judgment(workflow: str) -> AgentJudgment:
    value: Workflow = workflow if workflow in {
        "planning",
        "measurement",
        "assurance",
        "procurement",
        "dispatch",
        "cross_module",
        "four_module",
        "unsupported",
    } else "unsupported"
    return AgentJudgment(
        kind="workflow_classification",
        value=value,
        basis="structured_model_plan",
        matched_terms=[],
    )
