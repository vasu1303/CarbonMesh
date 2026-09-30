from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import safe_error_body
from app.api.router import api_router
from app.db.session import dispose_engine
from app.modules.agents.tasks import agent_task_registry


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Release database resources without performing startup DDL."""
    yield
    try:
        await agent_task_registry.shutdown()
    finally:
        await dispose_engine()


app = FastAPI(title="CarbonMesh API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(SQLAlchemyError)
async def handle_database_error(request: Request, _: SQLAlchemyError) -> JSONResponse:
    """Return a retryable error without exposing SQL, parameters, or credentials."""

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


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, _: Exception) -> JSONResponse:
    """Keep unexpected implementation details out of public responses."""

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


app.include_router(api_router, prefix="/api")
