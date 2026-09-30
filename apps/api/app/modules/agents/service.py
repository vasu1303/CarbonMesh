from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol
from uuid import UUID, uuid4

from app.db.models.carbon import AgentRun
from app.modules.agents.orchestration import (
    BudgetCounter,
    Classification,
    build_frozen_context,
    build_plan,
    classify_workflow,
    required_context_fields,
)
from app.modules.agents.repository import ResolvedContextReferences
from app.modules.agents.schemas import (
    AgentBudget,
    AgentContextRequest,
    AgentEvent,
    AgentJudgment,
    AgentQueryAccepted,
    AgentQueryRequest,
    AgentRunPayload,
    AgentRunResult,
    AgentTelemetry,
    FrozenContextEnvelope,
    StageTelemetry,
    Workflow,
)
from app.modules.agents.workflow import (
    ResolvedWorkflowInputs,
    WorkflowExecutionError,
    WorkflowOutcome,
    WorkflowStop,
)


class AgentRunStore(Protocol):
    async def resolve_context_references(
        self,
        *,
        company_id: UUID,
        actor_id: UUID,
        site_id: UUID | None,
        reporting_period_id: UUID | None,
    ) -> ResolvedContextReferences: ...

    async def add(self, run: AgentRun) -> None: ...

    async def get(self, *, company_id: UUID, run_id: UUID) -> AgentRun | None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class AgentDomainWorkflow(Protocol):
    async def resolve(
        self,
        *,
        context: AgentContextRequest,
        workflow: Workflow,
    ) -> ResolvedWorkflowInputs: ...

    async def execute_procurement(
        self,
        *,
        inputs: ResolvedWorkflowInputs,
        context: AgentContextRequest,
        frozen_context: FrozenContextEnvelope,
        agent_run_id: UUID,
    ) -> WorkflowOutcome: ...


class AgentRunNotFoundError(LookupError):
    def __init__(self) -> None:
        super().__init__("agent run was not found")


class EventRecorder:
    def __init__(self, events: list[AgentEvent] | None = None) -> None:
        self.events = list(events or [])

    def add(self, name: str, data: dict[str, object]) -> None:
        self.events.append(
            AgentEvent.model_validate(
                {
                    "sequence": len(self.events) + 1,
                    "name": name,
                    "occurred_at": datetime.now(UTC),
                    "data": data,
                }
            )
        )


class AgentRunService:
    def __init__(self, repository: AgentRunStore, workflow: AgentDomainWorkflow) -> None:
        self._repository = repository
        self._workflow = workflow

    async def start(
        self,
        request: AgentQueryRequest,
        *,
        trace_id: str | None = None,
    ) -> AgentQueryAccepted:
        """Freeze inputs and commit a visible running row before detached execution."""

        started_clock = perf_counter()
        started_at = datetime.now(UTC)
        run_id = uuid4()
        trace_id = trace_id or f"cm-{uuid4().hex}"
        classification = classify_workflow(request.query)
        budget = AgentBudget()
        counter = BudgetCounter(budget)
        recorder = EventRecorder()
        stage_timings: list[StageTelemetry] = []
        rows_processed = 0
        recorder.add(
            "run.started",
            {
                "run_id": str(run_id),
                "workflow": classification.workflow,
                "budget": budget.model_dump(mode="json"),
                "trace_id": trace_id,
            },
        )

        context_clock = perf_counter()
        recorder.add("stage.started", {"stage": "context", "attempt": 1})
        counter.consume_tool()
        recorder.add(
            "tool.started",
            {
                "tool_id": "T01",
                "tool_name": "resolve_context",
                "input": {
                    "company_id": str(request.context.company_id),
                    "actor_id": str(request.context.actor_id),
                    "has_site": request.context.site_id is not None,
                    "has_reporting_period": request.context.reporting_period_id is not None,
                },
            },
        )
        references = await self._repository.resolve_context_references(
            company_id=request.context.company_id,
            actor_id=request.context.actor_id,
            site_id=request.context.site_id,
            reporting_period_id=request.context.reporting_period_id,
        )
        rows_processed += references.rows_resolved
        missing_fields = required_context_fields(classification.workflow, request.context)
        context_elapsed_ms = _elapsed_ms(context_clock)
        recorder.add(
            "tool.completed",
            {
                "tool_id": "T01",
                "tool_name": "resolve_context",
                "duration_ms": context_elapsed_ms,
                "row_count": references.rows_resolved,
                "output": {"missing_fields": missing_fields},
            },
        )
        recorder.add(
            "stage.completed",
            {"stage": "context", "attempt": 1, "elapsed_ms": context_elapsed_ms},
        )
        stage_timings.append(StageTelemetry(stage="context", elapsed_ms=context_elapsed_ms))

        resolved: ResolvedWorkflowInputs | None = None
        initial_stop: WorkflowStop | None = None
        if classification.workflow == "unsupported":
            initial_stop = _unsupported_workflow_stop()
        elif missing_fields:
            initial_stop = _missing_context_stop(missing_fields)
        else:
            resolution_clock = perf_counter()
            recorder.add("stage.started", {"stage": "input_resolution", "attempt": 1})
            counter.consume_tool()
            recorder.add(
                "tool.started",
                {
                    "tool_id": "T03",
                    "tool_name": "find_verified_workflow_inputs",
                    "input": {
                        "site_id": str(request.context.site_id),
                        "reporting_period_id": str(request.context.reporting_period_id),
                        "material_scope": request.context.material_scope,
                    },
                },
            )
            try:
                resolved = await self._workflow.resolve(
                    context=request.context,
                    workflow=classification.workflow,
                )
            except WorkflowStop as error:
                initial_stop = error
            resolution_elapsed_ms = _elapsed_ms(resolution_clock)
            resolved_rows = resolved.rows_resolved if resolved else 0
            rows_processed += resolved_rows
            recorder.add(
                "tool.completed",
                {
                    "tool_id": "T03",
                    "tool_name": "find_verified_workflow_inputs",
                    "duration_ms": resolution_elapsed_ms,
                    "row_count": resolved_rows,
                    "output": {
                        "resolved": resolved is not None,
                        "terminal_state": (
                            initial_stop.terminal_state if initial_stop is not None else None
                        ),
                    },
                },
            )
            recorder.add(
                "stage.completed",
                {
                    "stage": "input_resolution",
                    "attempt": 1,
                    "elapsed_ms": resolution_elapsed_ms,
                },
            )
            stage_timings.append(
                StageTelemetry(stage="input_resolution", elapsed_ms=resolution_elapsed_ms)
            )

        frozen_context = build_frozen_context(
            request_context=request.context,
            actor_role=references.actor_role,
            query=request.query,
            workflow=classification.workflow,
            resolved=resolved.context if resolved else None,
        )
        judgment = _classification_judgment(classification)
        partial_payload = AgentRunPayload(
            message="The bounded deterministic workflow is queued for in-process execution.",
            judgments=[judgment],
            missing_fields=initial_stop.missing_fields if initial_stop else [],
            unsupported_reason=(
                classification.unsupported_reason
                if initial_stop is not None and initial_stop.terminal_state == "unsupported"
                else None
            ),
        )
        plan = build_plan(
            classification.workflow,
            partial_payload.missing_fields,
            executed=False,
        )
        telemetry = _telemetry(
            trace_id=trace_id,
            frozen_context=frozen_context,
            counter=counter,
            rows_processed=rows_processed,
            elapsed_ms=_elapsed_ms(started_clock),
            stage_timings=stage_timings,
            recorder=recorder,
        )
        run = _new_run(
            run_id=run_id,
            request=request,
            trace_id=trace_id,
            classification=classification,
            frozen_context=frozen_context,
            plan=plan.model_dump(mode="json"),
            payload=partial_payload,
            telemetry=telemetry,
            counter=counter,
            started_at=started_at,
        )
        await self._repository.add(run)
        await self._repository.commit()
        return AgentQueryAccepted(run_id=run_id, trace_id=trace_id)

    async def execute(self, *, request: AgentQueryRequest, run_id: UUID) -> None:
        """Execute a previously committed run in this service's independent session."""

        run = await self._repository.get(company_id=request.context.company_id, run_id=run_id)
        if run is None or run.terminal_state != "running":
            return

        classification = classify_workflow(request.query)
        frozen_context = FrozenContextEnvelope.model_validate(run.context_envelope)
        telemetry = AgentTelemetry.model_validate(run.telemetry)
        existing_payload = AgentRunPayload.model_validate(run.result)
        judgment = (
            existing_payload.judgments[0]
            if existing_payload.judgments
            else _classification_judgment(classification)
        )
        counter = BudgetCounter(
            AgentBudget(),
            model_calls=run.model_calls,
            tool_calls=run.tool_calls,
            repairs=run.retry_count,
        )
        recorder = EventRecorder(telemetry.events)
        stage_timings = list(telemetry.stage_timings)
        rows_processed = telemetry.rows_processed
        company_id = run.company_id
        persisted_run_id = run.id

        try:
            await self._execute_loaded(
                run=run,
                request=request,
                classification=classification,
                frozen_context=frozen_context,
                judgment=judgment,
                counter=counter,
                recorder=recorder,
                stage_timings=stage_timings,
                rows_processed=rows_processed,
            )
        except WorkflowExecutionError as error:
            await self._mark_failed(
                company_id=company_id,
                run_id=persisted_run_id,
                classification=classification,
                frozen_context=frozen_context,
                judgment=judgment,
                counter=counter,
                recorder=recorder,
                stage_timings=stage_timings,
                rows_processed=rows_processed,
                code=error.code,
                message=error.message,
            )
        except Exception:  # noqa: BLE001 - boundary persists a sanitized terminal failure
            await self._mark_failed(
                company_id=company_id,
                run_id=persisted_run_id,
                classification=classification,
                frozen_context=frozen_context,
                judgment=judgment,
                counter=counter,
                recorder=recorder,
                stage_timings=stage_timings,
                rows_processed=rows_processed,
                code="agent_workflow_failed",
                message="The bounded agent workflow failed safely.",
            )

    async def _execute_loaded(
        self,
        *,
        run: AgentRun,
        request: AgentQueryRequest,
        classification: Classification,
        frozen_context: FrozenContextEnvelope,
        judgment: AgentJudgment,
        counter: BudgetCounter,
        recorder: EventRecorder,
        stage_timings: list[StageTelemetry],
        rows_processed: int,
    ) -> None:
        missing_fields = required_context_fields(classification.workflow, request.context)
        stop: WorkflowStop | None = None
        resolved: ResolvedWorkflowInputs | None = None
        if classification.workflow == "unsupported":
            stop = _unsupported_workflow_stop()
        elif missing_fields:
            stop = _missing_context_stop(missing_fields)
        else:
            execution_context = _bind_resolved_context(request.context, frozen_context)
            try:
                resolved = await self._workflow.resolve(
                    context=execution_context,
                    workflow=classification.workflow,
                )
            except WorkflowStop as error:
                stop = error

        outcome: WorkflowOutcome | None = None
        if stop is None and resolved is not None:
            stage = "measurement" if classification.workflow == "measurement" else "procurement"
            stage_clock = perf_counter()
            recorder.add("stage.started", {"stage": stage, "attempt": 1})
            await self._persist_running_progress(
                run=run,
                stage=stage,
                frozen_context=frozen_context,
                classification=classification,
                judgment=judgment,
                counter=counter,
                recorder=recorder,
                stage_timings=stage_timings,
                rows_processed=rows_processed,
            )
            if classification.workflow == "measurement":
                try:
                    outcome = WorkflowOutcome(
                        terminal_state="completed",
                        message=(
                            "Returned an existing verified Measurement fact bound to its "
                            "immutable ledger event."
                        ),
                        facts=[resolved.measurement_fact()],
                    )
                except WorkflowStop as error:
                    stop = error
            else:
                try:
                    outcome = await self._workflow.execute_procurement(
                        inputs=resolved,
                        context=request.context,
                        frozen_context=frozen_context,
                        agent_run_id=run.id,
                    )
                except WorkflowStop as error:
                    stop = error
                    reloaded_run = await self._repository.get(
                        company_id=request.context.company_id,
                        run_id=run.id,
                    )
                    if reloaded_run is None:  # pragma: no cover - persisted-run invariant
                        raise AgentRunNotFoundError
                    run = reloaded_run

            stage_elapsed_ms = _elapsed_ms(stage_clock)
            if outcome is not None:
                rows_processed += outcome.rows_processed
                for operation in outcome.tool_operations:
                    counter.consume_tool()
                    common = {
                        "tool_id": operation.tool_id,
                        "tool_name": operation.tool_name,
                    }
                    recorder.add(
                        "tool.started",
                        {
                            **common,
                            "input": {
                                "carbon_measurement_id": str(resolved.measurement.id),
                                "current_product_id": str(resolved.current_product.id),
                                "method_definition_id": (
                                    str(resolved.method.id) if resolved.method else None
                                ),
                                "analysis_signature": frozen_context.analysis_signature,
                            },
                            "observation": "typed_service_operation",
                        },
                    )
                    recorder.add(
                        "tool.completed",
                        {
                            **common,
                            "duration_ms": stage_elapsed_ms,
                            "row_count": operation.row_count,
                            "output": {
                                "terminal_state": outcome.terminal_state,
                                "recommendation_id": (
                                    str(outcome.recommendation.recommendation_id)
                                    if outcome.recommendation
                                    else None
                                ),
                            },
                        },
                    )
            recorder.add(
                "stage.completed",
                {
                    "stage": stage,
                    "attempt": 1,
                    "elapsed_ms": stage_elapsed_ms,
                    "terminal_state": (
                        outcome.terminal_state if outcome is not None else stop.terminal_state
                    ),
                },
            )
            stage_timings.append(StageTelemetry(stage=stage, elapsed_ms=stage_elapsed_ms))

        await self._finalize(
            run=run,
            classification=classification,
            frozen_context=frozen_context,
            judgment=judgment,
            counter=counter,
            recorder=recorder,
            stage_timings=stage_timings,
            rows_processed=rows_processed,
            outcome=outcome,
            stop=stop,
        )

    async def _persist_running_progress(
        self,
        *,
        run: AgentRun,
        stage: str,
        frozen_context: FrozenContextEnvelope,
        classification: Classification,
        judgment: AgentJudgment,
        counter: BudgetCounter,
        recorder: EventRecorder,
        stage_timings: list[StageTelemetry],
        rows_processed: int,
    ) -> None:
        payload = AgentRunPayload(
            message=f"Deterministic {stage.title()} execution is in progress.",
            judgments=[judgment],
        )
        run.stage = stage
        run.plan = build_plan(classification.workflow, [], executed=False).model_dump(mode="json")
        run.result = payload.model_dump(mode="json")
        run.telemetry = _telemetry(
            trace_id=run.trace_id,
            frozen_context=frozen_context,
            counter=counter,
            rows_processed=rows_processed,
            elapsed_ms=_run_elapsed_ms(run),
            stage_timings=stage_timings,
            recorder=recorder,
        ).model_dump(mode="json")
        run.tool_calls = counter.tool_calls
        await self._repository.commit()

    async def _finalize(
        self,
        *,
        run: AgentRun,
        classification: Classification,
        frozen_context: FrozenContextEnvelope,
        judgment: AgentJudgment,
        counter: BudgetCounter,
        recorder: EventRecorder,
        stage_timings: list[StageTelemetry],
        rows_processed: int,
        outcome: WorkflowOutcome | None,
        stop: WorkflowStop | None,
    ) -> None:
        if outcome is not None:
            terminal_state = outcome.terminal_state
            payload = AgentRunPayload(
                message=outcome.message,
                facts=outcome.facts,
                judgments=[judgment],
                approval_requirement=outcome.approval_requirement,
                recommendation=outcome.recommendation,
            )
            for fact in outcome.facts:
                recorder.add(
                    "fact.created",
                    {
                        "fact_id": str(fact.fact_id),
                        "metric_key": fact.metric_key,
                        "display_value": fact.display_value,
                        "ledger_event_id": str(fact.ledger_event_id),
                    },
                )
            if outcome.approval_requirement.required:
                recorder.add(
                    "approval.required",
                    {
                        "approval_id": str(outcome.approval_requirement.approval_id),
                        "recommendation_id": str(outcome.approval_requirement.recommendation_id),
                        "preview_hash": outcome.approval_requirement.preview_hash,
                    },
                )
        else:
            if stop is None:  # pragma: no cover - orchestration invariant
                raise RuntimeError("bounded workflow produced neither an outcome nor a stop")
            terminal_state = stop.terminal_state
            payload = AgentRunPayload(
                message=stop.message,
                judgments=[judgment],
                missing_fields=stop.missing_fields,
                unsupported_reason=(
                    classification.unsupported_reason if terminal_state == "unsupported" else None
                ),
            )
            recorder.add(
                "validation.warning",
                {
                    "code": stop.code,
                    "missing_fields": stop.missing_fields,
                    "action": stop.message,
                },
            )

        elapsed_ms = _run_elapsed_ms(run)
        if terminal_state == "completed":
            recorder.add(
                "run.completed",
                {
                    "facts": [fact.model_dump(mode="json") for fact in payload.facts],
                    "recommendation": (
                        payload.recommendation.model_dump(mode="json")
                        if payload.recommendation
                        else None
                    ),
                    "approval": payload.approval_requirement.model_dump(mode="json"),
                    "telemetry": {
                        "model_calls": counter.model_calls,
                        "tool_calls": counter.tool_calls,
                        "retry_count": 0,
                        "elapsed_ms": elapsed_ms,
                    },
                    "sustainability_impact": (
                        {
                            "avoided_kgco2e": str(payload.recommendation.avoided_kgco2e),
                            "reduction_pct": str(payload.recommendation.reduction_pct),
                        }
                        if payload.recommendation
                        else None
                    ),
                    "execution_mode": "deterministic_services",
                },
            )
        else:
            recorder.add(
                "run.stopped",
                {
                    "terminal_state": terminal_state,
                    "reason": payload.unsupported_reason or payload.message,
                    "actionable_reason": payload.message,
                },
            )

        plan = build_plan(
            classification.workflow,
            payload.missing_fields,
            executed=outcome is not None,
        )
        telemetry = _telemetry(
            trace_id=run.trace_id,
            frozen_context=frozen_context,
            counter=counter,
            rows_processed=rows_processed,
            elapsed_ms=elapsed_ms,
            stage_timings=stage_timings,
            recorder=recorder,
        )
        run.stage = "completed" if terminal_state == "completed" else "stopped"
        run.terminal_state = terminal_state
        run.plan = plan.model_dump(mode="json")
        run.result = payload.model_dump(mode="json")
        run.telemetry = telemetry.model_dump(mode="json")
        run.model_calls = counter.model_calls
        run.tool_calls = counter.tool_calls
        run.retry_count = counter.repairs
        run.completed_at = datetime.now(UTC)
        run.error_code = (
            None
            if terminal_state == "completed"
            else stop.code
            if stop is not None
            else terminal_state
        )
        await self._repository.commit()

    async def get(self, *, company_id: UUID, run_id: UUID) -> AgentRunResult:
        run = await self._repository.get(company_id=company_id, run_id=run_id)
        if run is None:
            raise AgentRunNotFoundError
        payload = (
            AgentRunPayload.model_validate(run.result)
            if run.result is not None
            else AgentRunPayload(message="The run is still in progress.")
        )
        return AgentRunResult(
            run_id=run.id,
            trace_id=run.trace_id,
            workflow=run.workflow,
            stage=run.stage,
            terminal_state=run.terminal_state,
            context=FrozenContextEnvelope.model_validate(run.context_envelope),
            plan=run.plan,
            facts=payload.facts,
            judgments=payload.judgments,
            telemetry=AgentTelemetry.model_validate(run.telemetry),
            approval_requirement=payload.approval_requirement,
            recommendation=payload.recommendation,
            message=payload.message,
            missing_fields=payload.missing_fields,
            unsupported_reason=payload.unsupported_reason,
            error_code=run.error_code,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )

    async def _mark_failed(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        classification: Classification,
        frozen_context: FrozenContextEnvelope,
        judgment: AgentJudgment,
        counter: BudgetCounter,
        recorder: EventRecorder,
        stage_timings: list[StageTelemetry],
        rows_processed: int,
        code: str,
        message: str,
    ) -> None:
        await self._repository.rollback()
        persisted = await self._repository.get(company_id=company_id, run_id=run_id)
        if persisted is None:
            return
        payload = AgentRunPayload(message=message, judgments=[judgment])
        recorder.add(
            "run.stopped",
            {
                "terminal_state": "failed",
                "reason": "A deterministic workflow dependency failed.",
                "actionable_reason": message,
            },
        )
        telemetry = _telemetry(
            trace_id=persisted.trace_id,
            frozen_context=frozen_context,
            counter=counter,
            rows_processed=rows_processed,
            elapsed_ms=_run_elapsed_ms(persisted),
            stage_timings=stage_timings,
            recorder=recorder,
        )
        persisted.stage = "stopped"
        persisted.terminal_state = "failed"
        persisted.plan = build_plan(classification.workflow, [], executed=False).model_dump(
            mode="json"
        )
        persisted.result = payload.model_dump(mode="json")
        persisted.telemetry = telemetry.model_dump(mode="json")
        persisted.model_calls = counter.model_calls
        persisted.tool_calls = counter.tool_calls
        persisted.retry_count = counter.repairs
        persisted.completed_at = datetime.now(UTC)
        persisted.error_code = code
        await self._repository.commit()


def _new_run(
    *,
    run_id: UUID,
    request: AgentQueryRequest,
    trace_id: str,
    classification: Classification,
    frozen_context: FrozenContextEnvelope,
    plan: dict[str, object],
    payload: AgentRunPayload,
    telemetry: AgentTelemetry,
    counter: BudgetCounter,
    started_at: datetime,
) -> AgentRun:
    return AgentRun(
        id=run_id,
        company_id=request.context.company_id,
        actor_id=request.context.actor_id,
        trace_id=trace_id,
        workflow=classification.workflow,
        stage="queued",
        terminal_state="running",
        context_envelope=frozen_context.model_dump(mode="json"),
        plan=plan,
        result=payload.model_dump(mode="json"),
        telemetry=telemetry.model_dump(mode="json"),
        model_calls=counter.model_calls,
        tool_calls=counter.tool_calls,
        retry_count=counter.repairs,
        started_at=started_at,
    )


def _telemetry(
    *,
    trace_id: str,
    frozen_context: FrozenContextEnvelope,
    counter: BudgetCounter,
    rows_processed: int,
    elapsed_ms: int,
    stage_timings: list[StageTelemetry],
    recorder: EventRecorder,
) -> AgentTelemetry:
    return AgentTelemetry(
        trace_id=trace_id,
        analysis_signature=frozen_context.analysis_signature,
        model_calls=counter.model_calls,
        tool_calls=counter.tool_calls,
        retry_count=counter.repairs,
        repairs=counter.repairs,
        rows_processed=rows_processed,
        elapsed_ms=elapsed_ms,
        stage_timings=stage_timings,
        events=recorder.events,
    )


def _classification_judgment(classification: Classification) -> AgentJudgment:
    return AgentJudgment(
        kind="workflow_classification",
        value=classification.workflow,
        basis="deterministic_keyword_rules",
        matched_terms=list(classification.matched_terms),
    )


def _unsupported_workflow_stop() -> WorkflowStop:
    return WorkflowStop(
        "unsupported",
        (
            "Rephrase the request as a Measurement, Procurement, or connected "
            "Measurement-to-Procurement workflow."
        ),
        code="unsupported_workflow",
    )


def _missing_context_stop(missing_fields: list[str]) -> WorkflowStop:
    return WorkflowStop(
        "needs_clarification",
        "Provide every missing frozen-context field and start a new bounded run.",
        missing_fields=missing_fields,
        code="context_incomplete",
    )


def _bind_resolved_context(
    context: AgentContextRequest,
    frozen_context: FrozenContextEnvelope,
) -> AgentContextRequest:
    if frozen_context.resolved is None:
        return context
    return context.model_copy(
        update={
            "carbon_measurement_id": frozen_context.resolved.carbon_measurement_id,
            "current_product_id": frozen_context.resolved.current_product_id,
            "method_definition_id": frozen_context.resolved.method_definition_id,
        }
    )


def _run_elapsed_ms(run: AgentRun) -> int:
    started_at = run.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return max(0, round((datetime.now(UTC) - started_at).total_seconds() * 1000))


def _elapsed_ms(started_clock: float) -> int:
    return max(0, round((perf_counter() - started_clock) * 1000))
