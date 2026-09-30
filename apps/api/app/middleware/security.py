from __future__ import annotations

from typing import Final

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.tracing import ensure_trace_id

MAX_REQUEST_BODY_BYTES: Final = 12 * 1024 * 1024


class _RequestBodyTooLarge(Exception):
    pass


class ApiSecurityMiddleware:
    """Bound request bodies and add low-risk API response hardening headers."""

    def __init__(self, app: ASGIApp, max_body_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        trace_id = ensure_trace_id(_decode_header(headers.get(b"x-trace-id")))
        content_length = _parse_content_length(headers.get(b"content-length"))
        if content_length is not None and content_length > self.max_body_bytes:
            await self._reject(scope, receive, send, trace_id)
            return

        received_bytes = 0
        response_started = False

        async def bounded_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def hardened_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                response_headers = list(message.get("headers", []))
                response_headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, bounded_receive, hardened_send)
        except _RequestBodyTooLarge:
            if response_started:  # pragma: no cover - defensive ASGI invariant
                raise
            await self._reject(scope, receive, send, trace_id)

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        trace_id: str,
    ) -> None:
        response = JSONResponse(
            status_code=413,
            headers={
                "X-Trace-ID": trace_id,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
            content={
                "detail": {
                    "code": "request_too_large",
                    "message": "The request body exceeds the API size limit.",
                    "trace_id": trace_id,
                    "retryable": False,
                    "field_details": [],
                }
            },
        )
        await response(scope, receive, send)


def _decode_header(value: bytes | None) -> str | None:
    if value is None:
        return None
    try:
        return value.decode("ascii")
    except UnicodeDecodeError:
        return None


def _parse_content_length(value: bytes | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
