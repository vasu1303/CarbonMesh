from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.demo.fixtures import _fixture_directory
from app.modules.dispatch.errors import DispatchError
from app.modules.dispatch.schemas import (
    CreateDispatchScenarioRequest,
    DispatchOptimizationResult,
    DispatchRecommendationResult,
    DispatchScenarioView,
    FlexibleLoadList,
    ForecastSyncRequest,
    ForecastSyncResult,
    OptimizeScenarioRequest,
)
from app.modules.dispatch.service import DispatchService
from app.modules.integrations.electricity_maps import (
    ElectricityMapsProvider,
    get_electricity_maps_client,
)
from app.modules.integrations.grid_forecast import ElectricityMapsFixtureClient

router = APIRouter()


def _demo_forecast_fixture_directory() -> Path:
    configured = os.getenv("CARBONMESH_DEMO_FIXTURE_DIR")
    if configured:
        return Path(configured)
    try:
        return _fixture_directory()
    except RuntimeError:
        pass
    # Container packaging may place assets under the process working directory.
    # A missing directory is reported by the fixture adapter only when fixture
    # mode is requested; module import and API startup remain safe.
    return Path.cwd() / "data" / "demo"


DEMO_FORECAST_FIXTURE_DIRECTORY = _demo_forecast_fixture_directory()


def _http_error(error: DispatchError, trace_id: str | None) -> HTTPException:
    return safe_http_error(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        trace_id=trace_id,
        retryable=error.retryable,
        field_details=error.field_details,
    )


@router.get(
    "/dispatch/loads",
    response_model=FlexibleLoadList,
    summary="List advisory flexible loads and operating constraints",
)
async def list_dispatch_loads(
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    site_id: UUID | None = None,
    active_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    trace_id: TraceIdHeader = None,
) -> FlexibleLoadList:
    try:
        return await DispatchService(session).list_loads(
            company_id=company_id,
            site_id=site_id,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
    except DispatchError as error:
        raise _http_error(error, trace_id) from error


@router.post(
    "/dispatch/forecasts/sync",
    response_model=ForecastSyncResult,
    summary="Sync and persist an immutable 24-hour grid forecast",
)
async def sync_dispatch_forecast(
    request: ForecastSyncRequest,
    session: DatabaseSession,
    provider: Annotated[ElectricityMapsProvider, Depends(get_electricity_maps_client)],
    trace_id: TraceIdHeader = None,
) -> ForecastSyncResult:
    try:
        selected_provider = (
            ElectricityMapsFixtureClient(DEMO_FORECAST_FIXTURE_DIRECTORY)
            if request.source_mode == "fixture"
            else provider
        )
        return await DispatchService(session).sync_forecast(
            request,
            provider=selected_provider,
        )
    except DispatchError as error:
        raise _http_error(error, trace_id) from error


@router.post(
    "/dispatch/scenarios",
    response_model=DispatchScenarioView,
    status_code=status.HTTP_201_CREATED,
    summary="Create a frozen advisory Dispatch scenario",
)
async def create_dispatch_scenario(
    request: CreateDispatchScenarioRequest,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> DispatchScenarioView:
    try:
        return await DispatchService(session).create_scenario(request)
    except DispatchError as error:
        raise _http_error(error, trace_id) from error


@router.post(
    "/dispatch/scenarios/{scenario_id}/optimize",
    response_model=DispatchOptimizationResult,
    summary="Enumerate feasible windows and create an advisory recommendation",
)
async def optimize_dispatch_scenario(
    scenario_id: UUID,
    request: OptimizeScenarioRequest,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> DispatchOptimizationResult:
    try:
        return await DispatchService(session).optimize_scenario(scenario_id, request)
    except DispatchError as error:
        raise _http_error(error, trace_id) from error


@router.get(
    "/dispatch/scenarios/{scenario_id}/recommendation",
    response_model=DispatchRecommendationResult,
    summary="Read an advisory Dispatch recommendation and exact approval preview",
)
async def get_dispatch_recommendation(
    scenario_id: UUID,
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    trace_id: TraceIdHeader = None,
) -> DispatchRecommendationResult:
    try:
        return await DispatchService(session).get_recommendation(
            company_id=company_id,
            scenario_id=scenario_id,
        )
    except DispatchError as error:
        raise _http_error(error, trace_id) from error
