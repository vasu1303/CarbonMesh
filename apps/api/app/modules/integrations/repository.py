from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import GridIntensityPoint
from app.db.models.core import DataSource, EvidenceItem, Site, SourceDocument
from app.db.models.semantic import MetricDefinition

ELECTRICITY_MAPS_EXTERNAL_REFERENCE = "electricity-maps:v4"
GRID_INTENSITY_METRIC_KEY = "electricity.grid_carbon_intensity"
GRID_INTENSITY_METRIC_VERSION = "2.0.0"
GRID_POINT_METHOD_PREFIX = "electricity-maps-v4"


async def get_site(
    session: AsyncSession,
    *,
    company_id: UUID,
    site_id: UUID,
) -> Site | None:
    return await session.scalar(
        select(Site).where(Site.company_id == company_id, Site.id == site_id)
    )


async def get_data_source(
    session: AsyncSession,
    *,
    company_id: UUID,
    site_id: UUID,
    is_synthetic: bool | None = None,
) -> DataSource | None:
    statement = select(DataSource).where(
        DataSource.company_id == company_id,
        DataSource.site_id == site_id,
        DataSource.external_reference == ELECTRICITY_MAPS_EXTERNAL_REFERENCE,
    )
    if is_synthetic is not None:
        statement = statement.where(DataSource.is_synthetic == is_synthetic)
    return await session.scalar(
        statement.order_by(DataSource.updated_at.desc(), DataSource.id.desc()).limit(1)
    )


def grid_history_lock_id(company_id: UUID) -> int:
    """Return a stable signed bigint key for company-scoped grid persistence."""

    digest = hashlib.sha256(f"grid-history:{company_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def acquire_grid_history_lock(session: AsyncSession, *, company_id: UUID) -> None:
    """Serialize history persistence without holding a lock during provider I/O."""

    await session.execute(select(func.pg_advisory_xact_lock(grid_history_lock_id(company_id))))


async def get_site_zone_hint(
    session: AsyncSession,
    *,
    company_id: UUID,
    site_id: UUID,
) -> str | None:
    result = await session.scalars(
        select(DataSource)
        .where(
            DataSource.company_id == company_id,
            DataSource.site_id == site_id,
            DataSource.status == "ready",
        )
        .order_by(DataSource.updated_at.desc(), DataSource.id.desc())
        .limit(25)
    )
    for source in result:
        for key in ("electricity_maps_zone", "zone"):
            value = source.configuration.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
    return None


async def get_metric_definition(
    session: AsyncSession,
    *,
    company_id: UUID,
) -> MetricDefinition | None:
    return await session.scalar(
        select(MetricDefinition).where(
            MetricDefinition.company_id == company_id,
            MetricDefinition.key == GRID_INTENSITY_METRIC_KEY,
            MetricDefinition.version == GRID_INTENSITY_METRIC_VERSION,
        )
    )


async def get_source_document_by_checksum(
    session: AsyncSession,
    *,
    company_id: UUID,
    checksum: str,
) -> SourceDocument | None:
    return await session.scalar(
        select(SourceDocument).where(
            SourceDocument.company_id == company_id,
            SourceDocument.checksum == checksum,
        )
    )


async def get_evidence_items(
    session: AsyncSession,
    *,
    company_id: UUID,
    source_document_id: UUID,
    locators: list[str],
) -> dict[str, EvidenceItem]:
    rows = await session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.company_id == company_id,
            EvidenceItem.source_document_id == source_document_id,
            EvidenceItem.locator.in_(locators),
        )
    )
    return {row.locator: row for row in rows}


async def get_grid_intensity_points(
    session: AsyncSession,
    *,
    company_id: UUID,
    site_id: UUID,
    zone: str,
    versions: list[tuple[datetime, str, str]],
) -> dict[tuple[datetime, str, str], GridIntensityPoint]:
    rows = await session.scalars(
        select(GridIntensityPoint).where(
            GridIntensityPoint.company_id == company_id,
            GridIntensityPoint.site_id == site_id,
            GridIntensityPoint.zone == zone,
            tuple_(
                GridIntensityPoint.observed_at,
                GridIntensityPoint.temporal_granularity,
                GridIntensityPoint.method_version,
            ).in_(versions),
        )
    )
    return {(row.observed_at, row.temporal_granularity, row.method_version): row for row in rows}


async def get_latest_grid_intensity_point(
    session: AsyncSession,
    *,
    company_id: UUID,
    site_id: UUID,
    zone: str,
) -> tuple[GridIntensityPoint, EvidenceItem, SourceDocument, DataSource] | None:
    row = await session.execute(
        select(GridIntensityPoint, EvidenceItem, SourceDocument, DataSource)
        .join(
            EvidenceItem,
            (EvidenceItem.company_id == GridIntensityPoint.company_id)
            & (EvidenceItem.id == GridIntensityPoint.evidence_item_id),
        )
        .join(
            SourceDocument,
            (SourceDocument.company_id == EvidenceItem.company_id)
            & (SourceDocument.id == EvidenceItem.source_document_id),
        )
        .join(
            DataSource,
            (DataSource.company_id == SourceDocument.company_id)
            & (DataSource.id == SourceDocument.data_source_id),
        )
        .where(
            GridIntensityPoint.company_id == company_id,
            GridIntensityPoint.site_id == site_id,
            DataSource.external_reference == ELECTRICITY_MAPS_EXTERNAL_REFERENCE,
            GridIntensityPoint.zone == zone,
        )
        .order_by(
            GridIntensityPoint.observed_at.desc(),
            GridIntensityPoint.provider_updated_at.desc().nullslast(),
            GridIntensityPoint.created_at.desc(),
        )
        .limit(1)
    )
    return row.one_or_none()
