from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Release database resources without performing startup DDL."""
    yield
    await dispose_engine()


app = FastAPI(title="CarbonMesh API", version="0.1.0", lifespan=lifespan)
app.include_router(api_router, prefix="/api")
