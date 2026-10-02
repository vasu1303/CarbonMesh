from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import repository
from app.modules.audit.schemas import (
    AuditEntityResult,
    AuditLineageEdge,
    AuditLineageEvent,
    AuditLineageSummary,
    AuditTimelineItem,
)
from app.modules.ledger.repository import list_events

MAX_AUDIT_LINEAGE_DEPTH = 25
MAX_AUDIT_LINEAGE_EVENTS = 200


class UnsupportedAuditEntityError(ValueError):
    pass


class AuditEntityNotFoundError(LookupError):
    pass


def _entity_aliases(entity_type: str) -> set[str]:
    aliases = {entity_type}
    if entity_type in {"measurement", "carbon_measurement"}:
        aliases.update({"measurement", "carbon_measurement"})
    if entity_type in {"scenario", "procurement_scenario"}:
        aliases.update({"scenario", "procurement_scenario"})
    return aliases


async def get_entity_audit(
    session: AsyncSession,
    *,
    company_id: UUID,
    entity_type: str,
    entity_id: UUID,
) -> AuditEntityResult:
    normalized_type = entity_type.strip().lower()
    if normalized_type not in repository.ENTITY_MODELS:
        raise UnsupportedAuditEntityError(normalized_type)

    resolved_company_id = await repository.resolve_entity_company(
        session,
        company_id=company_id,
        entity_type=normalized_type,
        entity_id=entity_id,
    )
    if resolved_company_id is None:
        raise AuditEntityNotFoundError(str(entity_id))

    direct_refs = {(alias, entity_id) for alias in _entity_aliases(normalized_type)}
    refs = set(direct_refs)
    if normalized_type == "recommendation":
        approval_ids = await repository.list_linked_approval_ids(
            session,
            company_id=company_id,
            recommendation_id=entity_id,
        )
        refs.update(("approval", approval_id) for approval_id in approval_ids)

    audit_entries = await repository.list_audit_entries(
        session,
        company_id=company_id,
        refs=refs,
    )
    direct_ledger_entries = await repository.list_ledger_entries(
        session,
        company_id=company_id,
        refs=refs,
    )
    direct_event_ids = {entry.id for entry in direct_ledger_entries}
    all_event_ids = set(direct_event_ids)
    frontier = set(direct_event_ids)
    edges_by_id = {}
    truncated = False
    for _ in range(MAX_AUDIT_LINEAGE_DEPTH):
        adjacent = await repository.list_adjacent_edges(
            session,
            company_id=company_id,
            event_ids=frontier,
        )
        if not adjacent:
            break
        edges_by_id.update((edge.id, edge) for edge in adjacent)
        neighbors = {
            event_id
            for edge in adjacent
            for event_id in (edge.parent_event_id, edge.child_event_id)
        } - all_event_ids
        available = MAX_AUDIT_LINEAGE_EVENTS - len(all_event_ids)
        if len(neighbors) > available:
            neighbors = set(sorted(neighbors, key=str)[:available])
            truncated = True
        all_event_ids.update(neighbors)
        frontier = neighbors
        if not frontier or len(all_event_ids) >= MAX_AUDIT_LINEAGE_EVENTS:
            truncated = truncated or bool(frontier)
            break
    else:
        truncated = bool(frontier)
    edges = [
        edge
        for edge in edges_by_id.values()
        if edge.parent_event_id in all_event_ids and edge.child_event_id in all_event_ids
    ]
    ledger_entries = await list_events(
        session,
        company_id=company_id,
        event_ids=all_event_ids,
    )

    timeline: list[AuditTimelineItem] = []
    for entry in audit_entries:
        timeline.append(
            AuditTimelineItem(
                id=entry.id,
                timestamp=entry.created_at,
                source="audit",
                action=entry.action,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                actor_id=entry.actor_id,
                trace_id=entry.trace_id,
                details=entry.details,
                direct=(entry.entity_type, entry.entity_id) in direct_refs,
            )
        )
    for entry in ledger_entries:
        timeline.append(
            AuditTimelineItem(
                id=entry.id,
                timestamp=entry.created_at,
                source="ledger",
                action=entry.event_type,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                actor_id=entry.created_by,
                payload_hash=entry.payload_hash,
                details=entry.payload,
                direct=(entry.entity_type, entry.entity_id) in direct_refs,
            )
        )
    timeline.sort(key=lambda item: (item.timestamp, str(item.id)))

    lineage_events = [
        AuditLineageEvent(
            id=entry.id,
            event_type=entry.event_type,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            payload_hash=entry.payload_hash,
            created_at=entry.created_at,
        )
        for entry in sorted(ledger_entries, key=lambda item: (item.created_at, str(item.id)))
    ]
    lineage_edges = [
        AuditLineageEdge(
            id=edge.id,
            parent_event_id=edge.parent_event_id,
            child_event_id=edge.child_event_id,
            relationship_type=edge.relationship_type,
            metadata=edge.edge_metadata,
        )
        for edge in edges
    ]
    return AuditEntityResult(
        entity_type=normalized_type,
        entity_id=entity_id,
        company_id=company_id,
        timeline=timeline,
        lineage=AuditLineageSummary(
            events=lineage_events,
            edges=lineage_edges,
            truncated=truncated,
        ),
    )
