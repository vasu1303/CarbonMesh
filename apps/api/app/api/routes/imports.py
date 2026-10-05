from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.imports.schemas import (
    ActivityImportRequest,
    ImportResult,
    SupplierImportRequest,
)
from app.modules.imports.service import (
    ImportIdempotencyConflict,
    ImportReferenceNotFound,
    ImportRunNotFound,
    ImportService,
    ImportServiceError,
    InvalidImportContext,
)

router = APIRouter(tags=["imports"])


@router.post(
    "/activities/import",
    response_model=ImportResult,
    status_code=status.HTTP_201_CREATED,
)
async def import_activity(
    request: ActivityImportRequest,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> ImportResult:
    """Import CSV/JSON activity rows and normalize accepted records."""
    try:
        service = ImportService(session, trace_id=trace_id)
        return await service.import_activity(request)
    except ImportReferenceNotFound as error:
        raise _http_error(status.HTTP_404_NOT_FOUND, error, trace_id) from error
    except ImportIdempotencyConflict as error:
        raise _http_error(status.HTTP_409_CONFLICT, error, trace_id) from error
    except InvalidImportContext as error:
        raise _http_error(status.HTTP_422_UNPROCESSABLE_ENTITY, error, trace_id) from error
    except IntegrityError as error:
        raise _database_error(
            status.HTTP_409_CONFLICT,
            code="import_conflict",
            message="The import conflicts with data already stored.",
            retryable=False,
            trace_id=trace_id,
        ) from error
    except SQLAlchemyError as error:
        raise _database_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            code="database_unavailable",
            message="The import database is temporarily unavailable.",
            retryable=True,
            trace_id=trace_id,
        ) from error


@router.post(
    "/imports/suppliers",
    response_model=ImportResult,
    status_code=status.HTTP_201_CREATED,
)
async def import_suppliers(
    request: SupplierImportRequest,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> ImportResult:
    """Import supplier products and bind each product to source evidence."""
    try:
        service = ImportService(session, trace_id=trace_id)
        return await service.import_suppliers(request)
    except ImportReferenceNotFound as error:
        raise _http_error(status.HTTP_404_NOT_FOUND, error, trace_id) from error
    except ImportIdempotencyConflict as error:
        raise _http_error(status.HTTP_409_CONFLICT, error, trace_id) from error
    except IntegrityError as error:
        raise _database_error(
            status.HTTP_409_CONFLICT,
            code="import_conflict",
            message="The import conflicts with data already stored.",
            retryable=False,
            trace_id=trace_id,
        ) from error
    except SQLAlchemyError as error:
        raise _database_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            code="database_unavailable",
            message="The import database is temporarily unavailable.",
            retryable=True,
            trace_id=trace_id,
        ) from error


@router.get("/imports/{import_id}", response_model=ImportResult)
async def get_import(
    import_id: UUID,
    company_id: Annotated[UUID, Query(description="Tenant owning the import run.")],
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> ImportResult:
    """Read an import run. The path identifier is the persisted DataSource ID."""
    try:
        return await ImportService(session).get_import(company_id=company_id, import_id=import_id)
    except ImportRunNotFound as error:
        raise _http_error(status.HTTP_404_NOT_FOUND, error, trace_id) from error
    except SQLAlchemyError as error:
        raise _database_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            code="database_unavailable",
            message="Import status is temporarily unavailable.",
            retryable=True,
            trace_id=trace_id,
        ) from error


def _http_error(
    status_code: int,
    error: ImportServiceError,
    trace_id: str | None,
) -> HTTPException:
    field_details: dict[str, str] = {}
    if error.field_name is not None:
        field_details[error.field_name] = str(error)
    return safe_http_error(
        status_code=status_code,
        code=error.code,
        message=str(error),
        trace_id=trace_id,
        retryable=False,
        field_details=field_details,
    )


def _database_error(
    status_code: int,
    *,
    code: str,
    message: str,
    retryable: bool,
    trace_id: str | None,
) -> HTTPException:
    return safe_http_error(
        status_code=status_code,
        code=code,
        message=message,
        trace_id=trace_id,
        retryable=retryable,
    )
