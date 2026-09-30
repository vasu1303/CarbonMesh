"""Reset endpoint for the explicitly synthetic CarbonMesh rehearsal dataset."""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import DatabaseConfigurationError, session_scope
from app.modules.demo.service import DemoResetBlockedError, reset_and_seed_demo

router = APIRouter()


class DemoSeedCounts(BaseModel):
    metrics: int
    activity_records: int
    supplier_products: int
    emission_factors: int


class DemoResetResponse(BaseModel):
    status: Literal["reset"]
    synthetic: Literal[True]
    company_id: UUID
    site_id: UUID
    reporting_period_id: UUID
    seeded: DemoSeedCounts


@router.post("/reset", response_model=DemoResetResponse)
async def reset_demo() -> DemoResetResponse:
    """Replace an empty/all-synthetic database with the stable POC fixture set."""
    try:
        async with session_scope() as session:
            summary = await reset_and_seed_demo(session)
    except DemoResetBlockedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "demo_reset_blocked",
                "message": str(error),
                "trace_id": str(uuid4()),
                "retryable": False,
            },
        ) from error
    except (DatabaseConfigurationError, SQLAlchemyError, OSError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "database_unavailable",
                "message": "The demo database could not be reset.",
                "trace_id": str(uuid4()),
                "retryable": True,
            },
        ) from error

    return DemoResetResponse(
        status="reset",
        synthetic=True,
        company_id=summary.company_id,
        site_id=summary.site_id,
        reporting_period_id=summary.reporting_period_id,
        seeded=DemoSeedCounts(
            metrics=summary.metric_count,
            activity_records=summary.activity_record_count,
            supplier_products=summary.supplier_product_count,
            emission_factors=summary.emission_factor_count,
        ),
    )
