"""Transactional reset and seed service for the synthetic POC environment."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.bootstrap import load_model_registry
from app.db.models.core import Company
from app.modules.demo.fixtures import (
    DEMO_COMPANY_ID,
    DEMO_PERIOD_ID,
    DEMO_SITE_ID,
    build_demo_records,
)

DEMO_RESET_LOCK_KEY = "carbonmesh.demo.reset.v1"


class DemoResetBlockedError(RuntimeError):
    """Raised when reset would destroy any non-synthetic tenant data."""


@dataclass(frozen=True, slots=True)
class DemoResetSummary:
    company_id: UUID
    site_id: UUID
    reporting_period_id: UUID
    metric_count: int
    activity_record_count: int
    supplier_product_count: int
    emission_factor_count: int


def _truncate_statement() -> str:
    """Build a statement only from the source-controlled SQLAlchemy catalogue."""
    metadata = load_model_registry()
    qualified_tables = ", ".join(
        f'"{table.schema}"."{table.name}"'
        for table in sorted(metadata.tables.values(), key=lambda item: item.fullname)
    )
    return f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE"


async def reset_and_seed_demo(session: AsyncSession) -> DemoResetSummary:
    """Atomically clear an all-synthetic database and install stable demo records."""
    async with session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": DEMO_RESET_LOCK_KEY},
        )
        # Prevent a non-synthetic tenant insert from racing the safety check and
        # being removed by the following TRUNCATE.
        await session.execute(
            text('LOCK TABLE "core"."companies" IN SHARE ROW EXCLUSIVE MODE')
        )
        non_synthetic_count = await session.scalar(
            select(func.count())
            .select_from(Company)
            .where(Company.is_synthetic.is_(False))
        )
        if non_synthetic_count:
            raise DemoResetBlockedError(
                "Demo reset is disabled while non-synthetic company data exists."
            )

        await session.execute(text(_truncate_statement()))
        # Models intentionally do not expose cascading ORM relationships. Flush
        # the small fixture in declared dependency order so every FK target is
        # present before its dependent row, independent of mapper sort details.
        for record in build_demo_records():
            session.add(record)
            await session.flush()

    return DemoResetSummary(
        company_id=DEMO_COMPANY_ID,
        site_id=DEMO_SITE_ID,
        reporting_period_id=DEMO_PERIOD_ID,
        metric_count=6,
        activity_record_count=1,
        supplier_product_count=3,
        emission_factor_count=1,
    )
