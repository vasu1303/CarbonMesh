from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.errors import request_validation_error_response
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
app.add_exception_handler(RequestValidationError, request_validation_error_response)
app.include_router(api_router, prefix="/api")
