from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import DatabaseConfigurationError, session_scope
from app.modules.measurement.schemas import (
    MeasurementCalculateRequest,
    MeasurementDetail,
    MeasurementErrorDetail,
    MeasurementErrorEnvelope,
    MeasurementListResponse,
    MeasurementResult,
    MeasurementStatus,
)
from app.modules.measurement.service import MeasurementService, MeasurementServiceError

router = APIRouter(prefix="/measurements", tags=["measurements"])


async def get_measurement_service() -> AsyncIterator[MeasurementService]:
    try:
        async with session_scope() as session:
            yield MeasurementService(session)
    except (DatabaseConfigurationError, SQLAlchemyError) as error:
        _raise_unexpected_error(trace_id=None, cause=error)


ERROR_RESPONSES = {
    404: {"model": MeasurementErrorEnvelope},
    409: {"model": MeasurementErrorEnvelope},
    422: {"model": MeasurementErrorEnvelope},
    500: {"model": MeasurementErrorEnvelope},
    503: {"model": MeasurementErrorEnvelope},
}


@router.post(
    "/calculate",
    response_model=MeasurementResult,
    responses=ERROR_RESPONSES,
)
async def calculate_measurement(
    request: MeasurementCalculateRequest,
    service: Annotated[MeasurementService, Depends(get_measurement_service)],
) -> MeasurementResult:
    try:
        return await service.calculate(request)
    except MeasurementServiceError as error:
        _raise_service_error(error)
    except SQLAlchemyError as error:
        _raise_unexpected_error(trace_id=request.trace_id, cause=error)


@router.get(
    "",
    response_model=MeasurementListResponse,
    responses={503: {"model": MeasurementErrorEnvelope}},
)
async def list_measurements(
    company_id: Annotated[UUID, Query(description="Tenant company identifier.")],
    service: Annotated[MeasurementService, Depends(get_measurement_service)],
    site_id: Annotated[UUID | None, Query()] = None,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
    category: Annotated[str | None, Query(min_length=1, max_length=150)] = None,
    status: Annotated[MeasurementStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MeasurementListResponse:
    try:
        return await service.list_measurements(
            company_id=company_id,
            site_id=site_id,
            reporting_period_id=reporting_period_id,
            category=category,
            status=status,
            limit=limit,
            offset=offset,
        )
    except MeasurementServiceError as error:
        _raise_service_error(error)
    except SQLAlchemyError as error:
        _raise_unexpected_error(trace_id=None, cause=error)


@router.get(
    "/{measurement_id}",
    response_model=MeasurementDetail,
    responses=ERROR_RESPONSES,
)
async def get_measurement(
    measurement_id: UUID,
    company_id: Annotated[UUID, Query(description="Tenant company identifier.")],
    service: Annotated[MeasurementService, Depends(get_measurement_service)],
) -> MeasurementDetail:
    try:
        return await service.get_measurement(
            company_id=company_id,
            measurement_id=measurement_id,
        )
    except MeasurementServiceError as error:
        _raise_service_error(error)
    except SQLAlchemyError as error:
        _raise_unexpected_error(trace_id=None, cause=error)


def _raise_service_error(error: MeasurementServiceError) -> None:
    detail = MeasurementErrorDetail(
        code=error.code,
        message=error.message,
        trace_id=error.trace_id,
        retryable=error.retryable,
        terminal_state=error.terminal_state,
        field_details=dict(error.field_details or {}),
    )
    raise HTTPException(
        status_code=error.status_code,
        detail=detail.model_dump(mode="json"),
    ) from error


def _raise_unexpected_error(*, trace_id: str | None, cause: Exception) -> None:
    detail = MeasurementErrorDetail(
        code="measurement_service_unavailable",
        message="The measurement operation could not be completed.",
        trace_id=trace_id or f"measurement-{uuid4()}",
        retryable=True,
        terminal_state="failed_validation",
        field_details={},
    )
    raise HTTPException(status_code=503, detail=detail.model_dump(mode="json")) from cause
