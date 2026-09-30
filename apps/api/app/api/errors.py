from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.core.tracing import ensure_trace_id


class SafeErrorBody(BaseModel):
    """Credential-safe error contract shared by every versioned API route."""

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


def safe_http_error(
    *,
    status_code: int,
    code: str,
    message: str,
    trace_id: str | None,
    retryable: bool = False,
    field_details: FieldDetails | None = None,
) -> HTTPException:
    """Build the one sanitized error envelope used at the HTTP boundary."""

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
    )


def safe_error_body(
    *,
    code: str,
    message: str,
    trace_id: str | None,
    retryable: bool = False,
    field_details: FieldDetails | None = None,
) -> SafeErrorBody:
    """Return a validated error body for route and global exception handlers."""

    return SafeErrorBody(
        code=code,
        message=message,
        trace_id=ensure_trace_id(trace_id),
        retryable=retryable,
        field_details=_normalize_field_details(field_details),
    )
