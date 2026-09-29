from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


@router.get("", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    return HealthResponse(status="ok", service="CarbonMesh API")
