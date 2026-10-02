import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import request_validation_error_response, safe_error_body
from app.api.router import api_router
from app.core.config import get_database_url, get_settings
from app.core.observability import RequestTelemetryMiddleware, configure_telemetry
from app.db.session import DatabaseConfigurationError, dispose_engine
from app.middleware.security import ApiSecurityMiddleware
from app.modules.agents.tasks import (
    agent_task_registry,
    sweep_orphaned_agent_runs,
)

logger = logging.getLogger(__name__)


async def run_agent_recovery_sweeper(
    application: FastAPI,
    *,
    stop_event: asyncio.Event,
) -> None:
    """Run best-effort periodic recovery without gating application startup."""

    try:
        # Imported lazily to keep the application composition acyclic and make
        # database access optional for health-only/local startup.
        from app.api.routes.agents import get_agent_run_service_factory
        from app.dependencies.database import get_db_session_factory

        session_provider = application.dependency_overrides.get(
            get_db_session_factory,
            get_db_session_factory,
        )
        service_provider = application.dependency_overrides.get(
            get_agent_run_service_factory,
            get_agent_run_service_factory,
        )
        if (
            session_provider is get_db_session_factory
            and get_database_url() is None
        ):
            return

        await sweep_orphaned_agent_runs(
            session_factory=session_provider(),
            service_factory=service_provider(),
            stop_event=stop_event,
            interval_seconds=get_settings().agent_recovery_interval_seconds,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - an expired lease remains recoverable
        logger.warning(
            "Agent orphan recovery was deferred after %s.",
            type(error).__name__,
        )


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Release database resources without performing startup DDL."""
    telemetry = configure_telemetry(get_settings())
    recovery_stop_event = asyncio.Event()
    agent_task_registry.spawn(
        run_agent_recovery_sweeper(
            application,
            stop_event=recovery_stop_event,
        ),
        name="agent-orphan-recovery",
        cancel_on_shutdown=True,
    )
    try:
        yield
    finally:
        recovery_stop_event.set()
        try:
            await agent_task_registry.shutdown()
        finally:
            await dispose_engine()
            if telemetry is not None:
                await asyncio.to_thread(telemetry.force_flush, 3000)


app = FastAPI(title="CarbonMesh API", version="0.1.0", lifespan=lifespan)
app.add_middleware(ApiSecurityMiddleware)
app.add_middleware(RequestTelemetryMiddleware)
if get_settings().cors_origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=get_settings().cors_origins,
        allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Trace-ID", "X-Demo-Reset-Token"],
        expose_headers=["X-Trace-ID", "Content-Disposition"],
    )
app.add_exception_handler(RequestValidationError, request_validation_error_response)


async def handle_database_error(
    request: Request,
    _: SQLAlchemyError | DatabaseConfigurationError,
) -> JSONResponse:
    body = safe_error_body(
        code="data_unavailable",
        message="Application data is temporarily unavailable.",
        trace_id=request.headers.get("X-Trace-ID"),
        retryable=True,
    )
    return JSONResponse(
        status_code=503,
        content={"detail": body.model_dump(mode="json")},
        headers={"X-Trace-ID": body.trace_id},
    )


async def handle_unexpected_error(request: Request, _: Exception) -> JSONResponse:
    body = safe_error_body(
        code="internal_error",
        message="The request could not be completed safely.",
        trace_id=request.headers.get("X-Trace-ID"),
        retryable=False,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": body.model_dump(mode="json")},
        headers={"X-Trace-ID": body.trace_id},
    )


app.add_exception_handler(SQLAlchemyError, handle_database_error)
app.add_exception_handler(DatabaseConfigurationError, handle_database_error)
app.add_exception_handler(Exception, handle_unexpected_error)
app.include_router(api_router, prefix="/api")


def authenticated_openapi() -> dict:
    """Describe both session transports without requiring credentials for public routes."""
    if app.openapi_schema is None:
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        schema.setdefault("components", {})["securitySchemes"] = {
            "BearerSession": {"type": "http", "scheme": "bearer"},
            "CookieSession": {
                "type": "apiKey", "in": "cookie", "name": "carbonmesh_session",
            },
        }
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                if method not in {"get", "post", "patch", "delete", "put", "head", "options"}:
                    continue
                if path in {"/api/health", "/api/health/ready"}:
                    continue
                if path == "/api/auth/session" and method != "get":
                    continue
                operation["security"] = [{"BearerSession": []}, {"CookieSession": []}]
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = authenticated_openapi
