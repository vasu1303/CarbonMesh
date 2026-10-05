from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.routes import agents as agent_routes
from app.modules.agents.context import build_frozen_context
from app.modules.agents.events import encode_sse_event
from app.modules.agents.repository import MeasurementActivityProductInput
from app.modules.agents.schemas import (
    AgentContextRequest,
    AgentEvent,
    AgentQueryRequest,
)
from app.modules.agents.tasks import AgentTaskRegistry
from app.modules.agents.workflow import (
    MAX_RESOLUTION_ROWS,
    MAX_SCORING_METHOD_ROWS,
    AgentWorkflowExecutor,
    WorkflowStop,
)


def _context() -> AgentContextRequest:
    return AgentContextRequest(
        company_id=uuid4(),
        actor_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        material_scope=["packaging tray"],
        constraints={
            "max_cost_increase_pct": "5",
            "max_lead_time_days": 20,
            "minimum_circularity_score": "50",
        },
    )


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
async def test_agent_task_registry_logs_only_safe_failure_metadata(caplog) -> None:
    registry = AgentTaskRegistry()

    async def failing_worker() -> None:
        raise RuntimeError("provider-secret-must-not-be-logged")

    caplog.set_level("ERROR", logger="app.modules.agents.tasks")
    registry.spawn(failing_worker(), name="agent-run-safe-id")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert "agent-run-safe-id" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "provider-secret-must-not-be-logged" not in caplog.text


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

    monkeypatch.setattr(agent_routes, "follow_persisted_sse_events", one_event)

    response = await agent_routes.stream_agent_run_events(
        run_id,
        session_factory=session_factory,  # type: ignore[arg-type]
        company_id=company_id,
        service_factory=FoundRunService,
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
async def test_agent_route_redacts_unexpected_workflow_failure() -> None:
    class FailingService:
        async def start(self, *_, **__):
            raise RuntimeError("secret database/provider diagnostic")

    session = FakeRouteSession()
    request = AgentQueryRequest(
        query="Measure the packaging footprint",
        context=_context(),
    )

    with pytest.raises(HTTPException) as caught:
        await agent_routes.start_agent_query(
            request,
            session,
            session_factory=None,
            service_factory=lambda _: FailingService(),
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
