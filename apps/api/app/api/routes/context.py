from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.modules.semantic.dependencies import get_semantic_service
from app.modules.semantic.schemas import (
    APIErrorDetail,
    APIErrorResponse,
    ContextEnvelope,
    ContextResolveRequest,
)
from app.modules.semantic.service import ContextResolutionError, SemanticService

router = APIRouter()


@router.post(
    "/resolve",
    response_model=ContextEnvelope,
    responses={
        404: {"model": APIErrorResponse},
        422: {"model": APIErrorResponse},
        503: {"model": APIErrorResponse},
    },
)
async def resolve_context(
    request: ContextResolveRequest,
    service: Annotated[SemanticService, Depends(get_semantic_service)],
) -> ContextEnvelope:
    try:
        return await service.resolve_context(request)
    except ContextResolutionError as error:
        status_code = 404 if error.code.endswith("_not_found") else 422
        detail = APIErrorDetail(
            code=error.code,
            message=error.message,
            trace_id=str(uuid4()),
            field_details=error.fields,
        )
        raise HTTPException(status_code=status_code, detail=detail.model_dump()) from error
