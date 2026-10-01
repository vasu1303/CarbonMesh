from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.carbon import CarbonMeasurement
from app.db.models.core import AuditLog, DataSource, EvidenceItem, SourceDocument
from app.db.models.ledger import FactBinding, LedgerEvent, LedgerEventEvidence, LineageEdge


def _search_predicates(
    *,
    company_id: UUID,
    event_type: str | None,
    entity_type: str | None,
    entity_id: UUID | None,
    agent_run_id: UUID | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> tuple[ColumnElement[bool], ...]:
    predicates: list[ColumnElement[bool]] = [LedgerEvent.company_id == company_id]
    if event_type is not None:
        predicates.append(LedgerEvent.event_type == event_type)
    if entity_type is not None:
        predicates.append(LedgerEvent.entity_type == entity_type)
    if entity_id is not None:
        predicates.append(LedgerEvent.entity_id == entity_id)
    if created_from is not None:
        predicates.append(LedgerEvent.created_at >= created_from)
    if created_to is not None:
        predicates.append(LedgerEvent.created_at <= created_to)
    if agent_run_id is not None:
        audit_match = (
            select(AuditLog.id)
            .where(
                AuditLog.company_id == LedgerEvent.company_id,
                AuditLog.agent_run_id == agent_run_id,
                AuditLog.entity_type == LedgerEvent.entity_type,
                AuditLog.entity_id == LedgerEvent.entity_id,
            )
            .correlate(LedgerEvent)
            .exists()
        )
        binding_match = (
            select(FactBinding.id)
            .where(
                FactBinding.company_id == LedgerEvent.company_id,
                FactBinding.agent_run_id == agent_run_id,
                FactBinding.ledger_event_id == LedgerEvent.id,
            )
            .correlate(LedgerEvent)
            .exists()
        )
        predicates.append(or_(audit_match, binding_match))
    return tuple(predicates)


async def search_ledger_events(
    session: AsyncSession,
    *,
    company_id: UUID,
    event_type: str | None,
    entity_type: str | None,
    entity_id: UUID | None,
    agent_run_id: UUID | None,
    created_from: datetime | None,
    created_to: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[LedgerEvent], int]:
    predicates = _search_predicates(
        company_id=company_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        agent_run_id=agent_run_id,
        created_from=created_from,
        created_to=created_to,
    )
    total = await session.scalar(select(func.count(LedgerEvent.id)).where(*predicates))
    result = await session.scalars(
        select(LedgerEvent)
        .where(*predicates)
        .order_by(LedgerEvent.created_at.desc(), LedgerEvent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result), int(total or 0)


async def get_ledger_event(
    session: AsyncSession,
    *,
    company_id: UUID,
    event_id: UUID,
) -> LedgerEvent | None:
    return await session.scalar(
        select(LedgerEvent).where(
            LedgerEvent.company_id == company_id,
            LedgerEvent.id == event_id,
        )
    )


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


async def list_bounded_event_evidence(
    session: AsyncSession,
    *,
    company_id: UUID,
    event_id: UUID,
    limit: int,
) -> list[tuple[LedgerEventEvidence, EvidenceItem, SourceDocument, DataSource]]:
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
            LedgerEventEvidence.ledger_event_id == event_id,
        )
        .order_by(LedgerEventEvidence.created_at, EvidenceItem.id)
        .limit(limit)
    )
    return list(rows)


async def list_parent_neighbors(
    session: AsyncSession,
    *,
    company_id: UUID,
    event_id: UUID,
    limit: int,
) -> list[tuple[LineageEdge, LedgerEvent]]:
    rows = await session.execute(
        select(LineageEdge, LedgerEvent)
        .join(
            LedgerEvent,
            (LedgerEvent.company_id == LineageEdge.company_id)
            & (LedgerEvent.id == LineageEdge.parent_event_id),
        )
        .where(
            LineageEdge.company_id == company_id,
            LineageEdge.child_event_id == event_id,
        )
        .order_by(LineageEdge.created_at, LineageEdge.id)
        .limit(limit)
    )
    return list(rows)


async def list_child_neighbors(
    session: AsyncSession,
    *,
    company_id: UUID,
    event_id: UUID,
    limit: int,
) -> list[tuple[LineageEdge, LedgerEvent]]:
    rows = await session.execute(
        select(LineageEdge, LedgerEvent)
        .join(
            LedgerEvent,
            (LedgerEvent.company_id == LineageEdge.company_id)
            & (LedgerEvent.id == LineageEdge.child_event_id),
        )
        .where(
            LineageEdge.company_id == company_id,
            LineageEdge.parent_event_id == event_id,
        )
        .order_by(LineageEdge.created_at, LineageEdge.id)
        .limit(limit)
    )
    return list(rows)
