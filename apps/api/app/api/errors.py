"""Shared safe error responses for failures raised before a domain service runs."""

from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def request_validation_error_response(
    _request: Request,
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

    trace_id = str(uuid4())
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
