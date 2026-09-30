"""Reset endpoint for the explicitly synthetic CarbonMesh rehearsal dataset."""

from __future__ import annotations

import secrets
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import safe_http_error
from app.core.config import get_settings
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
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


def authorize_demo_reset(
    reset_token: Annotated[
        str | None,
        Header(alias="X-Demo-Reset-Token", min_length=16, max_length=256),
    ] = None,
    trace_id: TraceIdHeader = None,
) -> None:
    """Keep the destructive reset unavailable without an operator secret."""

    configured = get_settings().demo_reset_token
    if configured is None or len(configured.get_secret_value()) < 16:
        raise safe_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="demo_reset_disabled",
            message="Demo reset is not enabled on this API instance.",
            trace_id=trace_id,
            retryable=False,
        )
    expected = configured.get_secret_value()
    if reset_token is None or not secrets.compare_digest(
        reset_token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise safe_http_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code="demo_reset_forbidden",
            message="Demo reset authorization failed.",
            trace_id=trace_id,
            retryable=False,
        )


@router.post("/reset", response_model=DemoResetResponse)
async def reset_demo(
    session: DatabaseSession,
    _authorized: Annotated[None, Depends(authorize_demo_reset)],
    trace_id: TraceIdHeader = None,
) -> DemoResetResponse:
    """Replace an empty/all-synthetic database with the stable POC fixture set."""
    try:
        summary = await reset_and_seed_demo(session)
    except DemoResetBlockedError as error:
        raise safe_http_error(
            status_code=status.HTTP_409_CONFLICT,
            code="demo_reset_blocked",
            message=str(error),
            trace_id=trace_id,
            retryable=False,
        ) from error
    except (SQLAlchemyError, OSError) as error:
        raise safe_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="database_unavailable",
            message="The demo database could not be reset.",
            trace_id=trace_id,
            retryable=True,
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
