from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.demo.fixtures import _fixture_directory
from app.modules.integrations.electricity_maps import (
    ElectricityMapsProvider,
    get_electricity_maps_client,
)
from app.modules.integrations.grid_forecast import ElectricityMapsFixtureClient
from app.modules.integrations.schemas import (
    ElectricityMapsTestResult,
    GridIntensitySyncRequest,
    GridIntensitySyncResult,
    LatestGridIntensityResult,
)
from app.modules.integrations.service import (
    IntegrationServiceError,
    get_latest_grid_intensity,
    sync_grid_intensity,
    test_electricity_maps,
)

router = APIRouter()


def _as_http_error(error: IntegrationServiceError, trace_id: str | None) -> HTTPException:
    return safe_http_error(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        retryable=error.retryable,
        trace_id=trace_id,
        field_details=error.field_details,
    )


@router.post(
    "/integrations/electricity-maps/test",
    response_model=ElectricityMapsTestResult,
)
async def test_electricity_maps_connection(
    provider: Annotated[ElectricityMapsProvider, Depends(get_electricity_maps_client)],
    max_zones: Annotated[int, Query(ge=1, le=100)] = 25,
    trace_id: TraceIdHeader = None,
) -> ElectricityMapsTestResult:
    try:
        return await test_electricity_maps(provider, max_zones=max_zones)
    except IntegrationServiceError as error:
        raise _as_http_error(error, trace_id) from error


@router.post(
    "/measurement/grid/history/sync",
    response_model=GridIntensitySyncResult,
)
async def synchronize_grid_intensity(
    site_id: UUID,
    company_id: UUID,
    session: DatabaseSession,
    provider: Annotated[ElectricityMapsProvider, Depends(get_electricity_maps_client)],
    request: Annotated[GridIntensitySyncRequest | None, Body()] = None,
    trace_id: TraceIdHeader = None,
) -> GridIntensitySyncResult:
    request = request or GridIntensitySyncRequest()
    selected_provider = (
        ElectricityMapsFixtureClient(_fixture_directory(), fixture_variant=request.fixture_variant)
        if request.mode == "fixture"
        else provider
    )
    try:
        return await sync_grid_intensity(
            session,
            company_id=company_id,
            site_id=site_id,
            request=request,
            provider=selected_provider,
        )
    except IntegrationServiceError as error:
        raise _as_http_error(error, trace_id) from error


@router.get(
    "/measurement/grid/latest",
    response_model=LatestGridIntensityResult,
)
async def read_latest_grid_intensity(
    site_id: UUID,
    company_id: UUID,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> LatestGridIntensityResult:
    try:
        return await get_latest_grid_intensity(
            session,
            company_id=company_id,
            site_id=site_id,
        )
    except IntegrationServiceError as error:
        raise _as_http_error(error, trace_id) from error
