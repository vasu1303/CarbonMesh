from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import safe_http_error
from app.api.routes.dispatch import DEMO_FORECAST_FIXTURE_DIRECTORY
from app.core.tracing import ensure_trace_id
from app.db.session import execution_session_scope
from app.dependencies.database import (
    DatabaseSession,
    DatabaseSessionFactory,
    DatabaseSessionFactoryDependency,
)
from app.dependencies.request import TraceIdHeader
from app.modules.agents.events import follow_persisted_sse_events
from app.modules.agents.fresh_tools import FreshAgentToolServicePort
from app.modules.agents.ledger_replay import DomainLedgerWriteReplay
from app.modules.agents.llm.contracts import AIProviderName
from app.modules.agents.repository import AgentRunRepository, ContextReferenceNotFoundError
from app.modules.agents.resume import ApprovalResumePort, GenericApprovalResumeObserver
from app.modules.agents.runtime import (
    AgentResumeError,
    GraphAgentRunNotFoundError,
    GraphAgentRunService,
)
from app.modules.agents.schemas import (
    AgentQueryAccepted,
    AgentQueryRequest,
    AgentResumeRequest,
    AgentResumeResult,
    AgentRunResult,
    AgentSustainabilityMetrics,
    Workflow,
)
from app.modules.agents.sustainability import (
    AgentSustainabilityRepository,
    AgentSustainabilityService,
    SustainabilityIntegrityError,
)
from app.modules.agents.tasks import agent_task_registry
from app.modules.agents.tools import AgentToolRegistry
from app.modules.agents.workflow import AgentWorkflowExecutor
from app.modules.assurance.service import AssuranceService
from app.modules.dispatch.service import DispatchService
from app.modules.integrations.electricity_maps import get_electricity_maps_client
from app.modules.integrations.grid_forecast import ElectricityMapsFixtureClient
from app.modules.integrations.schemas import GridIntensitySyncRequest
from app.modules.integrations.service import sync_grid_intensity
from app.modules.measurement.service import MeasurementService
from app.modules.procurement.service import ProcurementService

router = APIRouter(tags=["agent"])

type AgentRunServiceFactory = Callable[[AsyncSession], GraphAgentRunService]


def _approval_resume_port(session: AsyncSession) -> ApprovalResumePort:
    """Observe the shared approval service's exact, revalidated human decision."""

    return GenericApprovalResumeObserver(session)


def _service(session: AsyncSession) -> GraphAgentRunService:
    live_grid_provider = get_electricity_maps_client()
    fixture_grid_provider = ElectricityMapsFixtureClient(
        DEMO_FORECAST_FIXTURE_DIRECTORY
    )

    async def sync_history(context, arguments):
        provider = (
            ElectricityMapsFixtureClient(
                DEMO_FORECAST_FIXTURE_DIRECTORY, fixture_variant=arguments.fixture_variant,
            )
            if arguments.source_mode == "fixture"
            else live_grid_provider
        )
        return await sync_grid_intensity(
            session,
            company_id=context.company_id,
            site_id=context.site_id,
            request=GridIntensitySyncRequest(
                start=arguments.start,
                end=arguments.end,
                mode=arguments.source_mode,
                fixture_variant=arguments.fixture_variant,
            ),
            provider=provider,
        )

    service_port = FreshAgentToolServicePort(
        measurement_service=MeasurementService(session),
        procurement_service=ProcurementService(session),
        assurance_service=AssuranceService(session),
        dispatch_service=DispatchService(session),
        live_grid_provider=live_grid_provider,
        fixture_grid_provider=fixture_grid_provider,
        grid_history_sync=sync_history,
        ledger_event_write=DomainLedgerWriteReplay(AgentRunRepository(session)),
        workflow_resolver=AgentWorkflowExecutor(session),
    )
    return GraphAgentRunService(
        AgentRunRepository(session),
        tool_registry_factory=lambda: AgentToolRegistry(service_port),
        approval_resume=_approval_resume_port(session),
    )


def get_agent_run_service_factory() -> AgentRunServiceFactory:
    """Return the production composition root through an overridable FastAPI seam."""

    return _service


_AGENT_RUN_SERVICE_FACTORY_DEPENDENCY = Depends(get_agent_run_service_factory)


def _resolved_service_factory(value: object) -> AgentRunServiceFactory:
    """Keep direct route-unit calls compatible with FastAPI's ``Depends`` default."""

    return cast(AgentRunServiceFactory, value) if callable(value) else _service


def _sustainability_service(session: AsyncSession) -> AgentSustainabilityService:
    return AgentSustainabilityService(AgentSustainabilityRepository(session))


async def _execute_agent_run(
    *,
    session_factory: DatabaseSessionFactory,
    service_factory: AgentRunServiceFactory,
    request: AgentQueryRequest,
    run_id: UUID,
) -> None:
    async with execution_session_scope(session_factory) as session:
        await service_factory(session).execute(request=request, run_id=run_id)


async def _execute_resumed_agent_run(
    *,
    session_factory: DatabaseSessionFactory,
    service_factory: AgentRunServiceFactory,
    company_id: UUID,
    run_id: UUID,
) -> None:
    async with execution_session_scope(session_factory) as session:
        await service_factory(session).execute_resumed(
            company_id=company_id,
            run_id=run_id,
        )


@router.post(
    "/agent/requests",
    response_model=AgentQueryAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_agent_query(
    request: AgentQueryRequest,
    session: DatabaseSession,
    session_factory: DatabaseSessionFactoryDependency,
    trace_id: TraceIdHeader = None,
    service_factory: AgentRunServiceFactory = _AGENT_RUN_SERVICE_FACTORY_DEPENDENCY,
) -> AgentQueryAccepted:
    request_trace_id = ensure_trace_id(trace_id)
    selected_service_factory = _resolved_service_factory(service_factory)
    try:
        accepted = await selected_service_factory(session).start(
            request,
            trace_id=request_trace_id,
        )
        agent_task_registry.spawn(
            _execute_agent_run(
                session_factory=session_factory,
                service_factory=selected_service_factory,
                request=request,
                run_id=accepted.run_id,
            ),
            name=f"agent-run-{accepted.run_id}",
        )
        return accepted
    except ContextReferenceNotFoundError as error:
        await session.rollback()
        raise safe_http_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="context_reference_not_found",
            message=str(error),
            trace_id=request_trace_id,
            retryable=False,
            field_details=[
                {
                    "field": f"context.{error.entity_type}_id",
                    "message": "Reference is unavailable in the requested company.",
                }
            ],
        ) from error
    except SQLAlchemyError as error:
        await session.rollback()
        raise safe_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="agent_data_unavailable",
            message="Agent workflow data is temporarily unavailable.",
            trace_id=request_trace_id,
            retryable=True,
        ) from error
    except Exception as error:
        await session.rollback()
        raise safe_http_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="agent_workflow_failed",
            message="The bounded agent workflow failed safely.",
            trace_id=request_trace_id,
            retryable=False,
        ) from error


@router.get("/runs/{run_id}", response_model=AgentRunResult)
async def get_agent_run(
    run_id: UUID,
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    trace_id: TraceIdHeader = None,
    service_factory: AgentRunServiceFactory = _AGENT_RUN_SERVICE_FACTORY_DEPENDENCY,
) -> AgentRunResult:
    request_trace_id = ensure_trace_id(trace_id)
    try:
        return await _resolved_service_factory(service_factory)(session).get(
            company_id=company_id,
            run_id=run_id,
        )
    except GraphAgentRunNotFoundError as error:
        raise safe_http_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="agent_run_not_found",
            message="Agent run was not found.",
            trace_id=request_trace_id,
            retryable=False,
        ) from error
    except SQLAlchemyError as error:
        await session.rollback()
        raise safe_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="agent_data_unavailable",
            message="Agent run data is temporarily unavailable.",
            trace_id=request_trace_id,
            retryable=True,
        ) from error
    except Exception as error:
        await session.rollback()
        raise safe_http_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="agent_run_read_failed",
            message="The agent run could not be read safely.",
            trace_id=request_trace_id,
            retryable=False,
        ) from error


@router.post("/runs/{run_id}/resume", response_model=AgentResumeResult)
async def resume_agent_run(
    run_id: UUID,
    request: AgentResumeRequest,
    session: DatabaseSession,
    session_factory: DatabaseSessionFactoryDependency,
    trace_id: TraceIdHeader = None,
    service_factory: AgentRunServiceFactory = _AGENT_RUN_SERVICE_FACTORY_DEPENDENCY,
) -> AgentResumeResult:
    request_trace_id = ensure_trace_id(trace_id)
    selected_service_factory = _resolved_service_factory(service_factory)
    try:
        result = await selected_service_factory(session).resume(
            run_id=run_id,
            request=request,
        )
        if result.resumed and result.terminal_state == "running":
            agent_task_registry.spawn(
                _execute_resumed_agent_run(
                    session_factory=session_factory,
                    service_factory=selected_service_factory,
                    company_id=request.company_id,
                    run_id=run_id,
                ),
                name=f"agent-resume-{run_id}",
            )
        return result
    except GraphAgentRunNotFoundError as error:
        await session.rollback()
        raise safe_http_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="agent_run_not_found",
            message="Agent run was not found.",
            trace_id=request_trace_id,
            retryable=False,
        ) from error
    except ContextReferenceNotFoundError as error:
        await session.rollback()
        raise safe_http_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="context_reference_not_found",
            message=str(error),
            trace_id=request_trace_id,
            retryable=False,
            field_details=[
                {
                    "field": f"context.{error.entity_type}_id",
                    "message": "Reference is unavailable in the requested company.",
                }
            ],
        ) from error
    except AgentResumeError as error:
        await session.rollback()
        raise safe_http_error(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            trace_id=request_trace_id,
            retryable=False,
        ) from error
    except SQLAlchemyError as error:
        await session.rollback()
        raise safe_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="agent_resume_unavailable",
            message="The agent run cannot be resumed right now.",
            trace_id=request_trace_id,
            retryable=True,
        ) from error
    except Exception as error:
        await session.rollback()
        raise safe_http_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="agent_resume_failed",
            message="The agent run could not be resumed safely.",
            trace_id=request_trace_id,
            retryable=False,
        ) from error


@router.get(
    "/runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": (
                "Replay and follow committed run events until the persisted terminal state."
            ),
        }
    },
)
async def stream_agent_run_events(
    run_id: UUID,
    session_factory: DatabaseSessionFactoryDependency,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
    trace_id: TraceIdHeader = None,
    service_factory: AgentRunServiceFactory = _AGENT_RUN_SERVICE_FACTORY_DEPENDENCY,
) -> StreamingResponse:
    request_trace_id = ensure_trace_id(trace_id)
    try:
        async with session_factory() as preflight_session:
            await _resolved_service_factory(service_factory)(preflight_session).get(
                company_id=company_id,
                run_id=run_id,
            )
    except GraphAgentRunNotFoundError as error:
        raise safe_http_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="agent_run_not_found",
            message="Agent run was not found.",
            trace_id=request_trace_id,
            retryable=False,
        ) from error
    except SQLAlchemyError as error:
        raise safe_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="agent_data_unavailable",
            message="Agent event data is temporarily unavailable.",
            trace_id=request_trace_id,
            retryable=True,
        ) from error
    except Exception as error:
        raise safe_http_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="agent_event_stream_failed",
            message="The agent event stream could not be prepared safely.",
            trace_id=request_trace_id,
            retryable=False,
        ) from error

    return StreamingResponse(
        follow_persisted_sse_events(
            session_factory=session_factory,
            company_id=company_id,
            run_id=run_id,
            last_event_id=last_event_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/metrics/agent-sustainability",
    response_model=AgentSustainabilityMetrics,
)
async def get_agent_sustainability_metrics(
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    from_time: Annotated[datetime | None, Query(description="Inclusive UTC start time")] = None,
    to_time: Annotated[datetime | None, Query(description="Exclusive UTC end time")] = None,
    workflow: Annotated[Workflow | None, Query()] = None,
    provider: Annotated[AIProviderName | None, Query()] = None,
    trace_id: TraceIdHeader = None,
) -> AgentSustainabilityMetrics:
    request_trace_id = ensure_trace_id(trace_id)
    try:
        return await _sustainability_service(session).get_metrics(
            company_id=company_id,
            from_time=from_time,
            to_time=to_time,
            workflow=workflow,
            provider=provider,
        )
    except SustainabilityIntegrityError as error:
        raise safe_http_error(
            status_code=status.HTTP_409_CONFLICT,
            code="agent_metrics_fact_invalid",
            message="Projected benefits no longer match their approved ledger facts.",
            trace_id=request_trace_id,
            retryable=False,
        ) from error
    except ValueError as error:
        raise safe_http_error(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="agent_metrics_query_invalid",
            message=str(error),
            trace_id=request_trace_id,
            retryable=False,
        ) from error
    except SQLAlchemyError as error:
        await session.rollback()
        raise safe_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="agent_metrics_unavailable",
            message="Agent sustainability metrics are temporarily unavailable.",
            trace_id=request_trace_id,
            retryable=True,
        ) from error
    except Exception as error:
        await session.rollback()
        raise safe_http_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="agent_metrics_failed",
            message="Agent sustainability metrics could not be read safely.",
            trace_id=request_trace_id,
            retryable=False,
        ) from error
