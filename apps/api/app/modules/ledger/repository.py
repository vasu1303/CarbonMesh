from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import CarbonMeasurement
from app.db.models.core import DataSource, EvidenceItem, SourceDocument
from app.db.models.ledger import LedgerEvent, LedgerEventEvidence, LineageEdge


async def get_measurement(
    session: AsyncSession,
    *,
    company_id: UUID,
    measurement_id: UUID,
) -> CarbonMeasurement | None:
    return await session.scalar(
        select(CarbonMeasurement).where(
            CarbonMeasurement.company_id == company_id,
            CarbonMeasurement.id == measurement_id,
        )
    )


async def list_parent_edges(
    session: AsyncSession,
    *,
    company_id: UUID,
    child_ids: Collection[UUID],
) -> list[LineageEdge]:
    if not child_ids:
        return []
    result = await session.scalars(
        select(LineageEdge)
        .where(
            LineageEdge.company_id == company_id,
            LineageEdge.child_event_id.in_(child_ids),
        )
        .order_by(LineageEdge.created_at, LineageEdge.id)
    )
    return list(result)


async def list_events(
    session: AsyncSession,
    *,
    company_id: UUID,
    event_ids: Collection[UUID],
) -> list[LedgerEvent]:
    if not event_ids:
        return []
    result = await session.scalars(
        select(LedgerEvent).where(
            LedgerEvent.company_id == company_id,
            LedgerEvent.id.in_(event_ids),
        )
    )
    return list(result)


async def list_event_evidence(
    session: AsyncSession,
    *,
    company_id: UUID,
    event_ids: Collection[UUID],
) -> list[tuple[LedgerEventEvidence, EvidenceItem, SourceDocument, DataSource]]:
    if not event_ids:
        return []
    rows = await session.execute(
        select(LedgerEventEvidence, EvidenceItem, SourceDocument, DataSource)
        .join(
            EvidenceItem,
            (EvidenceItem.company_id == LedgerEventEvidence.company_id)
            & (EvidenceItem.id == LedgerEventEvidence.evidence_item_id),
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
            LedgerEventEvidence.company_id == company_id,
            LedgerEventEvidence.ledger_event_id.in_(event_ids),
        )
        .order_by(LedgerEventEvidence.created_at, EvidenceItem.id)
    )
    return list(rows)
