from fastapi import APIRouter, HTTPException, status

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.sources.errors import SourceUploadError
from app.modules.sources.schemas import SourceUploadRequest, SourceUploadResponse
from app.modules.sources.service import upload_source_document

router = APIRouter(tags=["sources"])


def _as_http_error(error: SourceUploadError, trace_id: str | None) -> HTTPException:
    return safe_http_error(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        trace_id=trace_id,
        retryable=error.retryable,
        field_details=error.field_details,
    )


@router.post(
    "/sources/upload",
    response_model=SourceUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_source(
    request: SourceUploadRequest,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> SourceUploadResponse:
    try:
        return await upload_source_document(session, request, trace_id=trace_id)
    except SourceUploadError as error:
        raise _as_http_error(error, trace_id) from error
