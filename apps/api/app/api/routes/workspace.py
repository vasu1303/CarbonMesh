from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.workspace.schemas import WorkspaceOptionsQuery, WorkspaceOptionsResult
from app.modules.workspace.service import list_workspace_options

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get(
    "/options",
    response_model=WorkspaceOptionsResult,
    summary="List tenant-scoped named workspace options",
)
async def workspace_options(
    query: Annotated[WorkspaceOptionsQuery, Query()],
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> WorkspaceOptionsResult:
    try:
        return await list_workspace_options(session, query)
    except SQLAlchemyError as error:
        raise safe_http_error(
            status_code=503,
            code="workspace_options_unavailable",
            message="Workspace options are temporarily unavailable.",
            trace_id=trace_id,
            retryable=True,
        ) from error
