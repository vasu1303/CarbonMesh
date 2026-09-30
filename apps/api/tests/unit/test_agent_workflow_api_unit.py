from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.routes import agents as agent_routes
from app.db.models.carbon import AgentRun
from app.modules.agents.events import encode_sse_event
from app.modules.agents.orchestration import (
    BudgetCounter,
    BudgetExhaustedError,
    build_frozen_context,
    classify_workflow,
)
from app.modules.agents.repository import (
    MeasurementActivityProductInput,
    ResolvedContextReferences,
)
from app.modules.agents.schemas import (
    AgentBudget,
    AgentContextRequest,
    AgentEvent,
    AgentFact,
    AgentQueryRequest,
    AgentRecommendation,
    ApprovalRequirement,
    ResolvedWorkflowContext,
)
from app.modules.agents.service import AgentRunService
from app.modules.agents.tasks import AgentTaskRegistry
from app.modules.agents.workflow import (
    MAX_RESOLUTION_ROWS,
    MAX_SCORING_METHOD_ROWS,
    AgentWorkflowExecutor,
    WorkflowExecutionError,
    WorkflowOutcome,
    WorkflowStop,
    WorkflowToolOperation,
)


def _context(*, complete: bool = True) -> AgentContextRequest:
    return AgentContextRequest(
        company_id=uuid4(),
        actor_id=uuid4(),
        site_id=uuid4() if complete else None,
        reporting_period_id=uuid4() if complete else None,
        material_scope=["packaging tray"] if complete else [],
        constraints=(
            {
                "max_cost_increase_pct": "5",
                "max_lead_time_days": 20,
                "minimum_circularity_score": "50",
            }
            if complete
            else {}
        ),
    )


def test_agent_classifier_routes_only_supported_poc_workflows() -> None:
    measurement = classify_workflow("Calculate Scope 3 emissions for Plant B")
    procurement = classify_workflow("Recommend a feasible supplier under the cost limit")
    connected = classify_workflow("Measure the footprint and recommend a supplier")
    excluded = classify_workflow("Dispatch equipment after checking emissions")

    assert measurement.workflow == "measurement"
    assert procurement.workflow == "procurement"
    assert connected.workflow == "cross_module"
    assert excluded.workflow == "unsupported"
    assert excluded.unsupported_reason is not None


def test_agent_analysis_signature_is_stable_and_query_bound() -> None:
    context = _context()

    first = build_frozen_context(
        request_context=context,
        actor_role="procurement_manager",
        query="Recommend a supplier",
        workflow="procurement",
    )
    repeated = build_frozen_context(
        request_context=context,
        actor_role="procurement_manager",
        query="Recommend a supplier",
        workflow="procurement",
    )
    changed = build_frozen_context(
        request_context=context,
        actor_role="procurement_manager",
        query="Recommend another supplier",
        workflow="procurement",
    )

    assert first.analysis_signature == repeated.analysis_signature
    assert first.request_hash == repeated.request_hash
    assert first.analysis_signature != changed.analysis_signature


def test_agent_budget_counter_stops_before_limit_is_exceeded() -> None:
    counter = BudgetCounter(AgentBudget(max_tool_calls=1))
    counter.consume_tool()

    with pytest.raises(BudgetExhaustedError, match="tool-call"):
        counter.consume_tool()

    assert counter.tool_calls == 1


@pytest.mark.asyncio
async def test_agent_task_registry_cancels_and_awaits_pending_work_on_shutdown() -> None:
    registry = AgentTaskRegistry()
    worker_finished = asyncio.Event()

    async def pending_worker() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            worker_finished.set()

    registry.spawn(pending_worker(), name="test-agent-run")
    await asyncio.sleep(0)
    await registry.shutdown(timeout_seconds=0)

    assert worker_finished.is_set()


@pytest.mark.asyncio
async def test_agent_service_persists_bounded_plan_and_terminal_event() -> None:
    repository = FakeAgentRunRepository(actor_role="procurement_manager")
    service = AgentRunService(repository, FakeAgentWorkflow())
    request = AgentQueryRequest(
        query="Measure the packaging footprint and recommend a lower-carbon supplier",
        context=_context(),
    )

    accepted = await service.start(request)
    running = await service.get(
        company_id=request.context.company_id,
        run_id=accepted.run_id,
    )

    assert accepted.terminal_state == "running"
    assert running.terminal_state == "running"
    assert running.stage == "queued"

    await service.execute(request=request, run_id=accepted.run_id)
    result = await service.get(
        company_id=request.context.company_id,
        run_id=accepted.run_id,
    )

    assert result.terminal_state == "completed"
    assert result.workflow == "cross_module"
    assert result.plan is not None
    assert [step.id for step in result.plan.steps] == [
        "resolve_context",
        "measurement",
        "procurement",
    ]
    assert result.telemetry.model_calls == 0
    assert result.telemetry.tool_calls == 6
    assert result.telemetry.retry_count == 0
    assert result.telemetry.events[-1].name == "run.completed"
    assert len(result.facts) == 3
    assert result.recommendation is not None
    assert result.approval_requirement.required is True
    assert repository.committed


@pytest.mark.asyncio
async def test_agent_no_feasible_run_never_reports_approval_preview_tool() -> None:
    repository = FakeAgentRunRepository(actor_role="procurement_manager")
    service = AgentRunService(repository, NoFeasibleAgentWorkflow())
    request = AgentQueryRequest(
        query="Measure the packaging footprint and recommend a feasible supplier",
        context=_context(),
    )

    accepted = await service.start(request)
    await service.execute(request=request, run_id=accepted.run_id)
    result = await service.get(
        company_id=request.context.company_id,
        run_id=accepted.run_id,
    )

    completed_tools = {
        event.data["tool_id"] for event in result.telemetry.events if event.name == "tool.completed"
    }
    assert result.terminal_state == "no_feasible_option"
    assert result.telemetry.tool_calls == 5
    assert completed_tools == {"T01", "T03", "T09", "T10", "T11"}
    assert result.telemetry.events[-1].name == "run.stopped"


@pytest.mark.asyncio
async def test_agent_service_stops_for_missing_procurement_context() -> None:
    repository = FakeAgentRunRepository(actor_role="procurement_manager")
    service = AgentRunService(repository, FakeAgentWorkflow())
    request = AgentQueryRequest(
        query="Recommend a supplier under the cost constraint",
        context=_context(complete=False),
    )

    accepted = await service.start(request)
    assert accepted.terminal_state == "running"

    await service.execute(request=request, run_id=accepted.run_id)
    result = await service.get(
        company_id=request.context.company_id,
        run_id=accepted.run_id,
    )

    assert result.terminal_state == "needs_clarification"
    assert "context.site_id" in result.missing_fields
    assert "context.constraints.max_cost_increase_pct" in result.missing_fields
    assert result.error_code == "context_incomplete"
    assert result.telemetry.events[-1].name == "run.stopped"


@pytest.mark.asyncio
async def test_agent_service_persists_safe_background_failure() -> None:
    repository = FakeAgentRunRepository(actor_role="procurement_manager")
    service = AgentRunService(repository, FailingAgentWorkflow())
    request = AgentQueryRequest(
        query="Measure the packaging footprint and recommend a supplier",
        context=_context(),
    )

    accepted = await service.start(request)
    await service.execute(request=request, run_id=accepted.run_id)
    result = await service.get(
        company_id=request.context.company_id,
        run_id=accepted.run_id,
    )

    assert result.terminal_state == "failed"
    assert result.error_code == "procurement_unavailable"
    assert result.message == "Deterministic Procurement is temporarily unavailable."
    assert result.telemetry.events[-1].name == "run.stopped"


def test_agent_sse_encodes_named_event_with_resume_id() -> None:
    event = AgentEvent(
        sequence=2,
        name="run.completed",
        occurred_at="2026-09-30T00:00:01Z",
        data={"facts": []},
    )

    encoded = encode_sse_event(event)

    assert encoded.startswith("id: 2\nevent: run.completed\n")
    assert encoded.endswith("\n\n")


@pytest.mark.asyncio
async def test_agent_sse_preflight_owns_a_short_lived_factory_session(monkeypatch) -> None:
    session = FakeFactorySession()
    session_factory = FakeSessionFactory(session)
    run_id = uuid4()
    company_id = uuid4()

    class FoundRunService:
        def __init__(self, service_session) -> None:
            assert service_session is session

        async def get(self, *, company_id: UUID, run_id: UUID):
            return SimpleNamespace(company_id=company_id, id=run_id)

    async def one_event(**kwargs):
        assert kwargs["session_factory"] is session_factory
        yield "event: run.completed\ndata: {}\n\n"

    monkeypatch.setattr(agent_routes, "_service", FoundRunService)
    monkeypatch.setattr(agent_routes, "follow_persisted_sse_events", one_event)

    response = await agent_routes.stream_agent_run_events(
        run_id,
        session_factory=session_factory,  # type: ignore[arg-type]
        company_id=company_id,
        last_event_id=None,
        trace_id="trace-safe",
    )

    assert session_factory.calls == 1
    assert session.entered
    assert session.exited
    assert [chunk async for chunk in response.body_iterator] == [
        "event: run.completed\ndata: {}\n\n"
    ]


@pytest.mark.asyncio
async def test_agent_route_redacts_unexpected_workflow_failure(monkeypatch) -> None:
    class FailingService:
        async def start(self, *_, **__):
            raise RuntimeError("secret database/provider diagnostic")

    session = FakeRouteSession()
    monkeypatch.setattr(agent_routes, "_service", lambda _: FailingService())
    request = AgentQueryRequest(
        query="Measure the packaging footprint",
        context=_context(),
    )

    with pytest.raises(HTTPException) as caught:
        await agent_routes.start_agent_query(
            request,
            session,
            session_factory=None,
            trace_id="trace-safe",
        )

    assert caught.value.status_code == 500
    assert caught.value.detail == {
        "code": "agent_workflow_failed",
        "message": "The bounded agent workflow failed safely.",
        "trace_id": "trace-safe",
        "retryable": False,
        "field_details": [],
    }
    assert "secret" not in str(caught.value.detail)
    assert session.rolled_back


@pytest.mark.asyncio
async def test_agent_executor_rejects_explicit_unsupported_scopes_before_querying() -> None:
    executor = AgentWorkflowExecutor(None)  # type: ignore[arg-type]
    unsupported_metric = _context().model_copy(
        update={"metric_keys": ["electricity.grid_carbon_intensity"]}
    )
    incomplete_metric_scope = _context().model_copy(
        update={"metric_keys": ["emissions.scope3.category1"]}
    )
    product_allowlist = _context().model_copy(update={"supplier_product_ids": [uuid4()]})

    with pytest.raises(WorkflowStop) as metric_stop:
        await executor.resolve(context=unsupported_metric, workflow="cross_module")
    with pytest.raises(WorkflowStop) as incomplete_scope_stop:
        await executor.resolve(context=incomplete_metric_scope, workflow="cross_module")
    with pytest.raises(WorkflowStop) as supplier_stop:
        await executor.resolve(context=product_allowlist, workflow="procurement")

    assert metric_stop.value.terminal_state == "unsupported"
    assert metric_stop.value.code == "metric_scope_unsupported"
    assert incomplete_scope_stop.value.terminal_state == "failed_validation"
    assert incomplete_scope_stop.value.code == "workflow_metric_scope_incomplete"
    assert supplier_stop.value.terminal_state == "unsupported"
    assert supplier_stop.value.code == "supplier_product_scope_unsupported"


@pytest.mark.asyncio
async def test_agent_executor_resolves_inputs_through_bounded_repository_methods() -> None:
    workflow_input = fake_workflow_input(material_code="PACKAGING_TRAY")
    method = SimpleNamespace(id=uuid4())
    repository = FakeAgentWorkflowRepository(
        inputs=[workflow_input],
        methods=[method],
    )
    executor = AgentWorkflowExecutor(None, repository=repository)  # type: ignore[arg-type]
    context = _context()

    resolved = await executor.resolve(context=context, workflow="cross_module")

    assert resolved.measurement is workflow_input.measurement
    assert resolved.activity is workflow_input.activity
    assert resolved.current_product is workflow_input.product
    assert resolved.method is method
    assert resolved.rows_resolved == 5
    assert repository.measurement_query == {
        "company_id": context.company_id,
        "site_id": context.site_id,
        "reporting_period_id": context.reporting_period_id,
        "metric_key": "emissions.scope3.category1",
        "carbon_measurement_id": None,
        "current_product_id": None,
        "limit": MAX_RESOLUTION_ROWS,
    }
    assert repository.method_query is not None
    assert repository.method_query["limit"] == MAX_SCORING_METHOD_ROWS


@pytest.mark.asyncio
async def test_agent_executor_preserves_measurement_and_method_ambiguity_stops() -> None:
    duplicate_inputs = [
        fake_workflow_input(material_code="PACKAGING_TRAY"),
        fake_workflow_input(material_code="PACKAGING-TRAY"),
    ]
    measurement_repository = FakeAgentWorkflowRepository(inputs=duplicate_inputs)
    measurement_executor = AgentWorkflowExecutor(  # type: ignore[arg-type]
        None,
        repository=measurement_repository,
    )

    with pytest.raises(WorkflowStop) as measurement_stop:
        await measurement_executor.resolve(context=_context(), workflow="cross_module")

    assert measurement_stop.value.terminal_state == "needs_clarification"
    assert measurement_stop.value.code == "ambiguous_measurement_context"
    assert measurement_repository.period_calls == 0

    method_repository = FakeAgentWorkflowRepository(
        inputs=[fake_workflow_input(material_code="PACKAGING_TRAY")],
        methods=[SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())],
    )
    method_executor = AgentWorkflowExecutor(  # type: ignore[arg-type]
        None,
        repository=method_repository,
    )

    with pytest.raises(WorkflowStop) as method_stop:
        await method_executor.resolve(context=_context(), workflow="procurement")

    assert method_stop.value.terminal_state == "needs_clarification"
    assert method_stop.value.code == "ambiguous_scoring_method"
    assert method_repository.method_query is not None
    assert method_repository.method_query["limit"] == MAX_SCORING_METHOD_ROWS


def fake_workflow_input(*, material_code: str) -> MeasurementActivityProductInput:
    return MeasurementActivityProductInput(
        measurement=SimpleNamespace(id=uuid4()),
        activity=SimpleNamespace(
            id=uuid4(),
            material_code=material_code,
            normalized_quantity=Decimal(12000),
            normalized_unit="kg",
            unit_cost=Decimal("1.00"),
            currency="USD",
        ),
        product=SimpleNamespace(
            id=uuid4(),
            unit_cost=Decimal("1.00"),
            currency="USD",
        ),
    )


class FakeAgentWorkflowRepository:
    def __init__(
        self,
        *,
        inputs: list[MeasurementActivityProductInput],
        methods: list[SimpleNamespace] | None = None,
    ) -> None:
        self.inputs = inputs
        self.methods = methods or []
        self.period = SimpleNamespace(
            start_date=date(2026, 7, 1),
            end_date=date(2026, 9, 30),
        )
        self.measurement_query: dict[str, object] | None = None
        self.method_query: dict[str, object] | None = None
        self.period_calls = 0

    async def list_measurement_inputs(self, **query):
        self.measurement_query = query
        return self.inputs

    async def get_reporting_period(self, **_):
        self.period_calls += 1
        return self.period

    async def list_scoring_methods(self, **query):
        self.method_query = query
        return self.methods


class FakeAgentRunRepository:
    def __init__(self, actor_role: str) -> None:
        self.actor_role = actor_role
        self.runs: dict[UUID, AgentRun] = {}
        self.committed = False

    async def resolve_context_references(self, **_) -> ResolvedContextReferences:
        return ResolvedContextReferences(actor_role=self.actor_role, rows_resolved=3)

    async def add(self, run: AgentRun) -> None:
        self.runs[run.id] = run

    async def get(self, *, company_id: UUID, run_id: UUID) -> AgentRun | None:
        run = self.runs.get(run_id)
        return run if run is not None and run.company_id == company_id else None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None


class FakeResolvedWorkflowInputs:
    def __init__(self) -> None:
        self.context = ResolvedWorkflowContext(
            carbon_measurement_id=uuid4(),
            activity_record_id=uuid4(),
            current_product_id=uuid4(),
            method_definition_id=uuid4(),
            quantity="12000",
            quantity_unit="kg",
            current_unit_cost="1.00",
            currency="USD",
        )
        self.measurement = SimpleNamespace(id=self.context.carbon_measurement_id)
        self.current_product = SimpleNamespace(id=self.context.current_product_id)
        self.method = SimpleNamespace(id=self.context.method_definition_id)
        self.rows_resolved = 5
        self._measurement_ledger_id = uuid4()

    def measurement_fact(self) -> AgentFact:
        return AgentFact(
            fact_id=self.context.carbon_measurement_id,
            metric_key="emissions.scope3.category1",
            display_value="33600.000000 kgCO2e",
            ledger_event_id=self._measurement_ledger_id,
        )


class FakeAgentWorkflow:
    async def resolve(self, **_) -> FakeResolvedWorkflowInputs:
        return FakeResolvedWorkflowInputs()

    async def execute_procurement(self, *, inputs, **_) -> WorkflowOutcome:
        ledger_event_id = uuid4()
        recommendation_id = uuid4()
        approval_id = uuid4()
        facts = [
            inputs.measurement_fact(),
            AgentFact(
                fact_id=recommendation_id,
                metric_key="procurement.projected_avoided_emissions",
                display_value="10800.000000 kgCO2e",
                ledger_event_id=ledger_event_id,
            ),
            AgentFact(
                fact_id=uuid4(),
                metric_key="procurement.cost_delta_pct",
                display_value="3.2000%",
                ledger_event_id=ledger_event_id,
            ),
        ]
        recommendation = AgentRecommendation(
            scenario_id=uuid4(),
            recommendation_id=recommendation_id,
            recommended_product_id=uuid4(),
            status="pending_approval",
            projected_footprint_kgco2e="22800",
            avoided_kgco2e="10800",
            reduction_pct="32.1429",
            cost_delta_pct="3.2",
            lead_time_delta_days=2,
            payload_hash="a" * 64,
            analysis_signature="b" * 64,
            ledger_event_id=ledger_event_id,
        )
        return WorkflowOutcome(
            terminal_state="completed",
            message="Executed deterministic workflow.",
            facts=facts,
            recommendation=recommendation,
            approval_requirement=ApprovalRequirement(
                required=True,
                approval_id=approval_id,
                recommendation_id=recommendation_id,
                preview_hash="c" * 64,
            ),
            rows_processed=3,
            tool_operations=(
                WorkflowToolOperation("T09", "list_supplier_alternatives", 3),
                WorkflowToolOperation("T10", "score_supplier_products", 3),
                WorkflowToolOperation("T11", "calculate_procurement_impact", 3),
                WorkflowToolOperation("T12", "create_approval_preview", 1),
            ),
        )


class FailingAgentWorkflow(FakeAgentWorkflow):
    async def execute_procurement(self, **_) -> WorkflowOutcome:
        raise WorkflowExecutionError(
            code="procurement_unavailable",
            message="Deterministic Procurement is temporarily unavailable.",
        )


class NoFeasibleAgentWorkflow(FakeAgentWorkflow):
    async def execute_procurement(self, *, inputs, **_) -> WorkflowOutcome:
        return WorkflowOutcome(
            terminal_state="no_feasible_option",
            message="No supplier product satisfies every frozen hard constraint.",
            facts=[inputs.measurement_fact()],
            rows_processed=2,
            tool_operations=(
                WorkflowToolOperation("T09", "list_supplier_alternatives", 2),
                WorkflowToolOperation("T10", "score_supplier_products", 2),
                WorkflowToolOperation("T11", "calculate_procurement_impact", 2),
            ),
        )


class FakeRouteSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeFactorySession:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *_):
        self.exited = True


class FakeSessionFactory:
    def __init__(self, session: FakeFactorySession) -> None:
        self.session = session
        self.calls = 0

    def __call__(self) -> FakeFactorySession:
        self.calls += 1
        return self.session
