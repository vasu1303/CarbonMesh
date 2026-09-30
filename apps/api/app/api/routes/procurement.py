from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.procurement.errors import ProcurementError
from app.modules.procurement.schemas import (
    AssessmentRunRequest,
    AssessmentRunResult,
    CreateScenarioRequest,
    ProcurementScenarioResult,
    RecommendationDetail,
    SupplierProductDetail,
    SupplierProductList,
)
from app.modules.procurement.service import ProcurementService

router = APIRouter()


def _http_error(error: ProcurementError, trace_id: str | None) -> HTTPException:
    return safe_http_error(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        trace_id=trace_id,
        retryable=error.retryable,
        field_details=error.field_details,
    )


@router.get(
    "/suppliers",
    response_model=SupplierProductList,
    summary="List supplier products",
)
async def list_supplier_products(
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    material_code: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(max_length=100)] = None,
    active_only: bool = True,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    trace_id: TraceIdHeader = None,
) -> SupplierProductList:
    try:
        return await ProcurementService(session).list_supplier_products(
            company_id=company_id,
            material_code=material_code,
            category=category,
            active_only=active_only,
            search=search,
            limit=limit,
            offset=offset,
        )
    except ProcurementError as error:
        raise _http_error(error, trace_id) from error


@router.get(
    "/suppliers/{product_id}",
    response_model=SupplierProductDetail,
    summary="Read supplier product and evidence",
)
async def get_supplier_product(
    product_id: UUID,
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    trace_id: TraceIdHeader = None,
) -> SupplierProductDetail:
    try:
        return await ProcurementService(session).get_supplier_product(
            company_id=company_id, product_id=product_id
        )
    except ProcurementError as error:
        raise _http_error(error, trace_id) from error


@router.post(
    "/procurement/assessments/run",
    response_model=AssessmentRunResult,
    summary="Run deterministic supplier-product assessments",
)
async def run_supplier_assessments(
    request: AssessmentRunRequest,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> AssessmentRunResult:
    try:
        return await ProcurementService(session).run_assessment(request)
    except ProcurementError as error:
        raise _http_error(error, trace_id) from error


@router.post(
    "/procurement/scenarios",
    response_model=ProcurementScenarioResult,
    status_code=status.HTTP_201_CREATED,
    summary="Create and assess a frozen procurement scenario",
)
async def create_procurement_scenario(
    request: CreateScenarioRequest,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> ProcurementScenarioResult:
    try:
        return await ProcurementService(session).create_scenario(request)
    except ProcurementError as error:
        raise _http_error(error, trace_id) from error


@router.get(
    "/procurement/scenarios/{scenario_id}",
    response_model=ProcurementScenarioResult,
    summary="Read a frozen procurement scenario",
)
async def get_procurement_scenario(
    scenario_id: UUID,
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    trace_id: TraceIdHeader = None,
) -> ProcurementScenarioResult:
    try:
        return await ProcurementService(session).get_scenario(
            company_id=company_id, scenario_id=scenario_id
        )
    except ProcurementError as error:
        raise _http_error(error, trace_id) from error


@router.get(
    "/procurement/recommendations/{recommendation_id}",
    response_model=RecommendationDetail,
    summary="Read recommendation facts, scores, evidence, and hashes",
)
async def get_procurement_recommendation(
    recommendation_id: UUID,
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    trace_id: TraceIdHeader = None,
) -> RecommendationDetail:
    try:
        return await ProcurementService(session).get_recommendation(
            company_id=company_id, recommendation_id=recommendation_id
        )
    except ProcurementError as error:
        raise _http_error(error, trace_id) from error
