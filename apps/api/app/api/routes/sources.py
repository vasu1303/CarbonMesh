from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import AuthenticatedActorId, TraceIdHeader
from app.modules.sources.errors import SourceUploadError
from app.modules.sources.schemas import (
    SourceIndexRequest,
    SourceIndexResponse,
    SourceUploadRequest,
    SourceUploadResponse,
)
from app.modules.sources.service import (
    index_source_evidence,
    read_source_content,
    upload_source_document,
)

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
    authenticated_actor: AuthenticatedActorId = None,
) -> SourceUploadResponse:
    if request.actor_id is None and authenticated_actor is not None:
        request = request.model_copy(update={"actor_id": authenticated_actor})
    try:
        return await upload_source_document(session, request, trace_id=trace_id)
    except SourceUploadError as error:
        raise _as_http_error(error, trace_id) from error


@router.get("/sources/{document_id}/content", response_class=Response)
async def download_source(
    document_id: UUID,
    session: DatabaseSession,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    trace_id: TraceIdHeader = None,
) -> Response:
    try:
        document, content = await read_source_content(
            session, company_id=company_id, document_id=document_id
        )
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(document.filename, safe='')}",
                "Cache-Control": "private, no-store",
                "ETag": f'"{document.checksum}"',
            },
        )
    except SourceUploadError as error:
        raise _as_http_error(error, trace_id) from error


@router.post("/sources/{document_id}/index", response_model=SourceIndexResponse)
async def index_source(
    document_id: UUID,
    request: SourceIndexRequest,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> SourceIndexResponse:
    try:
        return await index_source_evidence(
            session, document_id=document_id, request=request, trace_id=trace_id
        )
    except SourceUploadError as error:
        raise _as_http_error(error, trace_id) from error
