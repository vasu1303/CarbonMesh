from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query

from app.modules.semantic.dependencies import get_semantic_service
from app.modules.semantic.schemas import APIErrorDetail, APIErrorResponse, MetricDefinitionList
from app.modules.semantic.service import ContextResolutionError, SemanticService

router = APIRouter()


@router.get(
    "/metrics",
    response_model=MetricDefinitionList,
    responses={
        404: {"model": APIErrorResponse},
        422: {"model": APIErrorResponse},
        503: {"model": APIErrorResponse},
    },
)
async def list_metric_definitions(
    company_id: Annotated[UUID, Query(description="Tenant company identifier")],
    service: Annotated[SemanticService, Depends(get_semantic_service)],
    active_only: Annotated[bool, Query()] = True,
) -> MetricDefinitionList:
    try:
        return await service.list_metrics(company_id, active_only=active_only)
    except ContextResolutionError as error:
        _raise_http_error(error)


def _raise_http_error(error: ContextResolutionError) -> None:
    status_code = 404 if error.code.endswith("_not_found") else 422
    detail = APIErrorDetail(
        code=error.code,
        message=error.message,
        trace_id=str(uuid4()),
        field_details=error.fields,
    )
    raise HTTPException(status_code=status_code, detail=detail.model_dump()) from error
