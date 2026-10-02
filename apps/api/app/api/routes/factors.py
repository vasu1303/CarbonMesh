from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.factors.schemas import (
    FactorListResponse,
    FactorRegisterRequest,
    FactorRegisterResponse,
)
from app.modules.factors.service import FactorError, FactorService

router = APIRouter(tags=["emission-factors"])


@router.post("/emission-factors", response_model=FactorRegisterResponse, status_code=201)
async def register_factor(
    request: FactorRegisterRequest,
    session: DatabaseSession,
    response: Response,
    trace_id: TraceIdHeader = None,
):
    try:
        result = await FactorService(session).register(request, trace_id=trace_id)
        if result.replayed:
            response.status_code = 200
        return result
    except FactorError as error:
        raise safe_http_error(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            trace_id=trace_id,
            retryable=error.status_code == 503,
        ) from error


@router.get("/emission-factors", response_model=FactorListResponse)
async def list_factors(
    session: DatabaseSession,
    company_id: Annotated[UUID, Query()],
    material_code: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    product_code: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    trace_id: TraceIdHeader = None,
):
    try:
        return await FactorService(session).list(
            company_id=company_id,
            material_code=material_code,
            product_code=product_code,
            active=active,
            limit=limit,
            offset=offset,
        )
    except FactorError as error:
        raise safe_http_error(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            trace_id=trace_id,
            retryable=error.status_code == 503,
        ) from error
