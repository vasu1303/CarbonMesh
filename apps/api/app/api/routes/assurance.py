from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.assurance.errors import AssuranceError
from app.modules.assurance.schemas import (
    AssuranceEvidencePack,
    AssuranceStandardListResponse,
    DisclosureDraftCreateRequest,
    DisclosureDraftValidateRequest,
    DisclosureDraftValidationResult,
    DisclosureDraftView,
)
from app.modules.assurance.service import AssuranceService

router = APIRouter()


def _http_error(error: AssuranceError, trace_id: str | None) -> HTTPException:
    return safe_http_error(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        trace_id=trace_id,
        retryable=error.retryable,
        field_details=error.field_details,
    )


@router.get(
    "/assurance/standards",
    response_model=AssuranceStandardListResponse,
    summary="List disclosure standards and ordered requirements",
)
async def list_assurance_standards(
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    active_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    trace_id: TraceIdHeader = None,
) -> AssuranceStandardListResponse:
    try:
        return await AssuranceService(session).list_standards(
            company_id=company_id,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
    except AssuranceError as error:
        raise _http_error(error, trace_id) from error


@router.post(
    "/assurance/drafts",
    response_model=DisclosureDraftView,
    status_code=status.HTTP_201_CREATED,
    summary="Create an immutable-context disclosure draft",
)
async def create_assurance_draft(
    request: DisclosureDraftCreateRequest,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> DisclosureDraftView:
    try:
        return await AssuranceService(session).create_draft(request, trace_id=trace_id)
    except AssuranceError as error:
        raise _http_error(error, trace_id) from error


@router.get(
    "/assurance/drafts/{draft_id}",
    response_model=DisclosureDraftView,
    summary="Read atomic claims, citations, gaps, and approval state",
)
async def get_assurance_draft(
    draft_id: UUID,
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    trace_id: TraceIdHeader = None,
) -> DisclosureDraftView:
    try:
        return await AssuranceService(session).get_draft(
            company_id=company_id,
            draft_id=draft_id,
        )
    except AssuranceError as error:
        raise _http_error(error, trace_id) from error


@router.post(
    "/assurance/drafts/{draft_id}/validate",
    response_model=DisclosureDraftValidationResult,
    summary="Validate claims, citations, gaps, staleness, and approval eligibility",
)
async def validate_assurance_draft(
    draft_id: UUID,
    request: DisclosureDraftValidateRequest,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> DisclosureDraftValidationResult:
    try:
        return await AssuranceService(session).validate_draft(
            draft_id,
            request,
            trace_id=trace_id,
        )
    except AssuranceError as error:
        raise _http_error(error, trace_id) from error


@router.get(
    "/assurance/drafts/{draft_id}/evidence-pack",
    response_model=AssuranceEvidencePack,
    summary="Export a structured traceability manifest for a disclosure draft",
)
async def get_assurance_evidence_pack(
    draft_id: UUID,
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    trace_id: TraceIdHeader = None,
) -> AssuranceEvidencePack:
    try:
        return await AssuranceService(session).get_evidence_pack(
            company_id=company_id,
            draft_id=draft_id,
        )
    except AssuranceError as error:
        raise _http_error(error, trace_id) from error
