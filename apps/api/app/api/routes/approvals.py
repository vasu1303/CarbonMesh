from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.errors import safe_http_error
from app.core.tracing import ensure_trace_id
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.approvals.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResult,
    ApprovalDetail,
    ApprovalListResult,
    ApprovalStatus,
)
from app.modules.approvals.service import (
    ApprovalServiceError,
    decide_approval,
    get_approval,
    list_approvals,
)

router = APIRouter()


@router.get("/approvals", response_model=ApprovalListResult)
async def read_approvals(
    company_id: UUID,
    session: DatabaseSession,
    status: ApprovalStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApprovalListResult:
    return await list_approvals(
        session,
        company_id=company_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/approvals/{approval_id}", response_model=ApprovalDetail)
async def read_approval(
    approval_id: UUID,
    company_id: UUID,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> ApprovalDetail:
    request_trace_id = ensure_trace_id(trace_id)
    try:
        return await get_approval(session, company_id=company_id, approval_id=approval_id)
    except ApprovalServiceError as error:
        raise safe_http_error(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            trace_id=request_trace_id,
            field_details=error.field_details,
        ) from error


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalDecisionResult)
async def make_approval_decision(
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> ApprovalDecisionResult:
    request_trace_id = ensure_trace_id(trace_id)
    try:
        return await decide_approval(
            session,
            approval_id=approval_id,
            request=request,
            trace_id=request_trace_id,
        )
    except ApprovalServiceError as error:
        raise safe_http_error(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            trace_id=request_trace_id,
            field_details=error.field_details,
        ) from error
