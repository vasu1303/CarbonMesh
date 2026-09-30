from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import EmissionFactor
from app.db.models.core import DataSource, EvidenceItem, Site, SourceDocument
from app.db.models.semantic import MetricDefinition

ELECTRICITY_MAPS_EXTERNAL_REFERENCE = "electricity-maps:v4"
GRID_INTENSITY_METRIC_KEY = "electricity.grid_carbon_intensity"
GRID_INTENSITY_METRIC_VERSION = "1.0.0"
GRID_FACTOR_CODE = "electricity-maps-grid-intensity"


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
) -> DataSource | None:
    return await session.scalar(
        select(DataSource)
        .where(
            DataSource.company_id == company_id,
            DataSource.site_id == site_id,
            DataSource.external_reference == ELECTRICITY_MAPS_EXTERNAL_REFERENCE,
        )
        .order_by(DataSource.created_at.desc())
        .limit(1)
    )


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


async def get_evidence_item(
    session: AsyncSession,
    *,
    company_id: UUID,
    source_document_id: UUID,
    locator: str,
) -> EvidenceItem | None:
    return await session.scalar(
        select(EvidenceItem).where(
            EvidenceItem.company_id == company_id,
            EvidenceItem.source_document_id == source_document_id,
            EvidenceItem.locator == locator,
        )
    )


async def get_emission_factor(
    session: AsyncSession,
    *,
    company_id: UUID,
    version: str,
    geography: str,
) -> EmissionFactor | None:
    return await session.scalar(
        select(EmissionFactor).where(
            EmissionFactor.company_id == company_id,
            EmissionFactor.factor_code == GRID_FACTOR_CODE,
            EmissionFactor.version == version,
            EmissionFactor.geography == geography,
        )
    )


async def get_latest_grid_factor(
    session: AsyncSession,
    *,
    company_id: UUID,
    zone: str,
) -> tuple[EmissionFactor, EvidenceItem, SourceDocument, DataSource] | None:
    row = await session.execute(
        select(EmissionFactor, EvidenceItem, SourceDocument, DataSource)
        .join(
            EvidenceItem,
            (EvidenceItem.company_id == EmissionFactor.company_id)
            & (EvidenceItem.id == EmissionFactor.evidence_item_id),
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
            EmissionFactor.company_id == company_id,
            DataSource.external_reference == ELECTRICITY_MAPS_EXTERNAL_REFERENCE,
            EmissionFactor.factor_code == GRID_FACTOR_CODE,
            EmissionFactor.geography == zone,
            EmissionFactor.status == "active",
        )
        .order_by(EmissionFactor.version.desc(), EmissionFactor.created_at.desc())
        .limit(1)
    )
    return row.one_or_none()
