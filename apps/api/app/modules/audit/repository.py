from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import AgentRun, CarbonMeasurement
from app.db.models.core import AuditLog, DataSource, EvidenceItem, Site, SourceDocument
from app.db.models.ledger import LedgerEvent, LineageEdge
from app.db.models.procurement import (
    Approval,
    ProcurementScenario,
    Recommendation,
    Supplier,
    SupplierProduct,
)

ENTITY_MODELS = {
    "measurement": CarbonMeasurement,
    "carbon_measurement": CarbonMeasurement,
    "recommendation": Recommendation,
    "approval": Approval,
    "procurement_scenario": ProcurementScenario,
    "scenario": ProcurementScenario,
    "supplier": Supplier,
    "supplier_product": SupplierProduct,
    "agent_run": AgentRun,
    "site": Site,
    "data_source": DataSource,
    "source_document": SourceDocument,
    "evidence_item": EvidenceItem,
}


async def resolve_entity_company(
    session: AsyncSession,
    *,
    company_id: UUID,
    entity_type: str,
    entity_id: UUID,
) -> UUID | None:
    model = ENTITY_MODELS.get(entity_type)
    if model is None:
        return None
    return await session.scalar(
        select(model.company_id).where(
            model.company_id == company_id,
            model.id == entity_id,
        )
    )


async def list_linked_approval_ids(
    session: AsyncSession,
    *,
    company_id: UUID,
    recommendation_id: UUID,
) -> list[UUID]:
    result = await session.scalars(
        select(Approval.id).where(
            Approval.company_id == company_id,
            Approval.recommendation_id == recommendation_id,
        )
    )
    return list(result)


def _entity_predicate(model: type[AuditLog | LedgerEvent], refs: Collection[tuple[str, UUID]]):
    return or_(
        *(
            and_(model.entity_type == entity_type, model.entity_id == entity_id)
            for entity_type, entity_id in refs
        )
    )


async def list_audit_entries(
    session: AsyncSession,
    *,
    company_id: UUID,
    refs: Collection[tuple[str, UUID]],
) -> list[AuditLog]:
    if not refs:
        return []
    result = await session.scalars(
        select(AuditLog)
        .where(
            AuditLog.company_id == company_id,
            _entity_predicate(AuditLog, refs),
        )
        .order_by(AuditLog.created_at, AuditLog.id)
    )
    return list(result)


async def list_ledger_entries(
    session: AsyncSession,
    *,
    company_id: UUID,
    refs: Collection[tuple[str, UUID]],
) -> list[LedgerEvent]:
    if not refs:
        return []
    result = await session.scalars(
        select(LedgerEvent)
        .where(
            LedgerEvent.company_id == company_id,
            _entity_predicate(LedgerEvent, refs),
        )
        .order_by(LedgerEvent.created_at, LedgerEvent.id)
    )
    return list(result)


async def list_adjacent_edges(
    session: AsyncSession,
    *,
    company_id: UUID,
    event_ids: Collection[UUID],
) -> list[LineageEdge]:
    if not event_ids:
        return []
    result = await session.scalars(
        select(LineageEdge)
        .where(
            LineageEdge.company_id == company_id,
            or_(
                LineageEdge.parent_event_id.in_(event_ids),
                LineageEdge.child_event_id.in_(event_ids),
            ),
        )
        .order_by(LineageEdge.created_at, LineageEdge.id)
    )
    return list(result)


async def list_ledger_events_by_id(
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
