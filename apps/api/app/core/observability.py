"""OpenTelemetry spans with an explicit, non-sensitive attribute surface."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy import event
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings
from app.core.tracing import current_trace_id, ensure_trace_id

_provider: TracerProvider | None = None
logger = logging.getLogger("carbonmesh.telemetry")


@dataclass
class ExternalUsage:
    api_calls: int = 0
    cache_hits: int = 0
    retry_count: int = 0


_external_usage: ContextVar[ExternalUsage | None] = ContextVar("external_usage", default=None)


def begin_external_usage() -> None:
    """Start one execution segment; inherited tasks share the bounded mutable counter."""
    _external_usage.set(ExternalUsage())


def record_external_call(provider: str, *, retry: bool = False) -> None:
    usage = _external_usage.get()
    if usage is not None:
        usage.api_calls += 1
        usage.retry_count += int(retry)
    current = trace.get_current_span()
    current.add_event("external.request", {"provider": provider, "retry": retry})


def record_external_cache_hit(provider: str) -> None:
    usage = _external_usage.get()
    if usage is not None:
        usage.cache_hits += 1
    trace.get_current_span().add_event("external.cache_hit", {"provider": provider})


def take_external_usage() -> ExternalUsage:
    usage = _external_usage.get()
    if usage is None:
        return ExternalUsage()
    result = ExternalUsage(usage.api_calls, usage.cache_hits, usage.retry_count)
    usage.api_calls = usage.cache_hits = usage.retry_count = 0
    return result


def configure_telemetry(settings: Settings) -> TracerProvider | None:
    global _provider
    if not settings.telemetry_enabled:
        return None
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    if _provider is None:
        _provider = TracerProvider(resource=Resource.create({
            "service.name": settings.telemetry_service_name,
            "service.version": "0.1.0",
        }))
        if settings.telemetry_otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
                endpoint=settings.telemetry_otlp_endpoint, timeout=3,
            )))
    return _provider


def tracer():
    return (_provider.get_tracer("carbonmesh") if _provider is not None
            else trace.get_tracer("carbonmesh"))


@contextmanager
def span(name: str, attributes: dict | None = None):
    # Exception messages and stack traces can contain URLs, prompts, or SQL.
    with tracer().start_as_current_span(
        name, attributes=attributes or {}, record_exception=False, set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except BaseException as error:
            current.set_attribute("error.type", type(error).__name__)
            current.set_status(trace.StatusCode.ERROR)
            raise


def traced_operation(name: str):
    def decorate(function):
        @wraps(function)
        async def traced(*args, **kwargs):
            with span(name):
                return await function(*args, **kwargs)
        return traced
    return decorate


def record_event_span(name: str, attributes: dict, latency_ms: int = 0) -> None:
    ended = time.time_ns()
    current = tracer().start_span(
        name, attributes=attributes,
        start_time=ended - max(0, latency_ms) * 1_000_000,
    )
    current.end(end_time=ended)


class RequestTelemetryMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        correlation = ensure_trace_id(headers.get(b"x-trace-id", b"").decode("ascii", "ignore"))
        correlation_token = current_trace_id.set(correlation)
        # Ensure all nested routes/services use exactly the same correlation ID.
        scope["headers"] = [
            (key, value) for key, value in scope.get("headers", []) if key != b"x-trace-id"
        ] + [(b"x-trace-id", correlation.encode())]
        started = time.monotonic()
        status = 500
        with span("http.request", {"http.request.method": scope["method"],
                                   "carbonmesh.correlation_id": correlation}) as current:
            async def traced_send(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                    response_headers = [
                        (key, value) for key, value in message.get("headers", [])
                        if key.lower() != b"x-trace-id"
                    ]
                    response_headers.append((b"x-trace-id", correlation.encode()))
                    message = {**message, "headers": response_headers}
                await send(message)

            try:
                await self.app(scope, receive, traced_send)
            finally:
                route = getattr(scope.get("route"), "path", "unmatched")
                current.update_name(f"{scope['method']} {route}")
                current.set_attribute("http.route", route)
                current.set_attribute("http.response.status_code", status)
                if status >= 500:
                    current.set_status(trace.StatusCode.ERROR)
                trace_id = format(current.get_span_context().trace_id, "032x")
                logger.info(json.dumps({
                    "event": "http.completed", "correlation_id": correlation,
                    "trace_id": trace_id, "method": scope["method"], "route": route,
                    "status": status, "latency_ms": round((time.monotonic() - started) * 1000),
                }, separators=(",", ":")))
                current_trace_id.reset(correlation_token)


def instrument_database(engine) -> None:
    """Trace operation timing only: never SQL text, parameters, URLs, or credentials."""
    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def before(connection, cursor, statement, parameters, context, executemany):
        operation = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else "QUERY"
        if operation not in {"SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "BEGIN", "COMMIT"}:
            operation = "QUERY"
        manager = span(f"db.{operation.lower()}", {
            "db.system.name": "postgresql", "db.operation.name": operation,
        })
        connection.info.setdefault("carbonmesh_spans", []).append(manager)
        manager.__enter__()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def after(connection, cursor, statement, parameters, context, executemany):
        pending = connection.info.get("carbonmesh_spans", [])
        if pending:
            pending.pop().__exit__(None, None, None)

    @event.listens_for(engine.sync_engine, "handle_error")
    def failed(context):
        if context.connection is None:
            return
        pending = context.connection.info.get("carbonmesh_spans", [])
        if pending:
            error = context.original_exception
            pending.pop().__exit__(type(error), error, error.__traceback__)
