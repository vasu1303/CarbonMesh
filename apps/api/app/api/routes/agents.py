from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import safe_http_error
from app.core.tracing import ensure_trace_id
from app.dependencies.database import (
    DatabaseSession,
    DatabaseSessionFactory,
    DatabaseSessionFactoryDependency,
)
from app.dependencies.request import TraceIdHeader
from app.modules.agents.events import follow_persisted_sse_events
from app.modules.agents.repository import AgentRunRepository, ContextReferenceNotFoundError
from app.modules.agents.schemas import AgentQueryAccepted, AgentQueryRequest, AgentRunResult
from app.modules.agents.service import AgentRunNotFoundError, AgentRunService
from app.modules.agents.tasks import agent_task_registry
from app.modules.agents.workflow import AgentWorkflowExecutor

router = APIRouter(tags=["agent"])


def _service(session: AsyncSession) -> AgentRunService:
    return AgentRunService(AgentRunRepository(session), AgentWorkflowExecutor(session))


async def _execute_agent_run(
    *,
    session_factory: DatabaseSessionFactory,
    request: AgentQueryRequest,
    run_id: UUID,
) -> None:
    async with session_factory() as session:
        try:
            await _service(session).execute(request=request, run_id=run_id)
        except BaseException:
            await session.rollback()
            raise


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
) -> AgentQueryAccepted:
    request_trace_id = ensure_trace_id(trace_id)
    try:
        accepted = await _service(session).start(request, trace_id=request_trace_id)
        agent_task_registry.spawn(
            _execute_agent_run(
                session_factory=session_factory,
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
) -> AgentRunResult:
    request_trace_id = ensure_trace_id(trace_id)
    try:
        return await _service(session).get(company_id=company_id, run_id=run_id)
    except AgentRunNotFoundError as error:
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
) -> StreamingResponse:
    request_trace_id = ensure_trace_id(trace_id)
    try:
        async with session_factory() as preflight_session:
            await _service(preflight_session).get(company_id=company_id, run_id=run_id)
    except AgentRunNotFoundError as error:
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
