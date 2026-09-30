"""Shared, credential-safe error responses for every API boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.tracing import ensure_trace_id


class SafeErrorBody(BaseModel):
    code: str
    message: str
    trace_id: str
    retryable: bool
    field_details: list[dict[str, Any]] = Field(default_factory=list)


FieldDetails = Mapping[str, object] | Sequence[Mapping[str, object]]


def _normalize_field_details(details: FieldDetails | None) -> list[dict[str, object]]:
    if details is None:
        return []
    if isinstance(details, Mapping):
        return [{"field": str(field), "detail": detail} for field, detail in details.items()]
    return [dict(detail) for detail in details]


def safe_error_body(
    *,
    code: str,
    message: str,
    trace_id: str | None,
    retryable: bool = False,
    field_details: FieldDetails | None = None,
) -> SafeErrorBody:
    return SafeErrorBody(
        code=code,
        message=message,
        trace_id=ensure_trace_id(trace_id),
        retryable=retryable,
        field_details=_normalize_field_details(field_details),
    )


def safe_http_error(
    *,
    status_code: int,
    code: str,
    message: str,
    trace_id: str | None,
    retryable: bool = False,
    field_details: FieldDetails | None = None,
) -> HTTPException:
    body = safe_error_body(
        code=code,
        message=message,
        trace_id=trace_id,
        retryable=retryable,
        field_details=field_details,
    )
    return HTTPException(
        status_code=status_code,
        detail=body.model_dump(mode="json"),
        headers={"X-Trace-ID": body.trace_id},
    )


async def request_validation_error_response(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    """Return field-level validation details without echoing request inputs."""
    field_details: dict[str, str] = {}
    # FastAPI's wrapper exposes a version-neutral ``errors()`` signature. Only
    # copy location/message fields so request inputs and validation context are
    # never reflected into the response.
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ())) or "request"
        message = str(item.get("msg", "Invalid value."))
        if location in field_details:
            field_details[location] = f"{field_details[location]}; {message}"
        else:
            field_details[location] = message

    trace_id = ensure_trace_id(request.headers.get("X-Trace-ID"))
    return JSONResponse(
        status_code=422,
        headers={"X-Trace-ID": trace_id},
        content={
            "detail": {
                "code": "request_validation_error",
                "message": "The request did not satisfy the API contract.",
                "trace_id": trace_id,
                "retryable": False,
                "terminal_state": "validation_error",
                "field_details": field_details,
            }
        },
    )
