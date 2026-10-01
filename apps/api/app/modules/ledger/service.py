from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ledger import LedgerEvent, LedgerEventEvidence, LineageEdge
from app.modules.ledger import repository
from app.modules.ledger.schemas import (
    LedgerEventDetail,
    LedgerEventListResult,
    LedgerEventSummary,
    LedgerEvidenceSummary,
    LedgerLineageNeighbor,
    LineageEdgeResult,
    LineageNode,
    MeasurementLineageResult,
)

MAX_LINEAGE_DEPTH = 25
MAX_LINEAGE_EVENTS = 200
MAX_LEDGER_PAGE_SIZE = 100
MAX_LEDGER_OFFSET = 10_000
MAX_EVENT_EVIDENCE = 100
MAX_EVENT_NEIGHBORS = 100


class MeasurementNotFoundError(LookupError):
    """Raised when a measurement does not exist."""


class LedgerEventNotFoundError(LookupError):
    """Raised when an event is unavailable in the requested tenant."""


class InvalidLedgerQueryError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _event_summary(event: LedgerEvent) -> LedgerEventSummary:
    return LedgerEventSummary(
        id=event.id,
        company_id=event.company_id,
        event_type=event.event_type,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        payload_hash=event.payload_hash,
        analysis_signature=event.analysis_signature,
        created_by=event.created_by,
        supersedes_event_id=event.supersedes_event_id,
        created_at=event.created_at,
    )


def _normalize_filter(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise InvalidLedgerQueryError(field, "Filter must not be blank.")
    if len(normalized) > 100:
        raise InvalidLedgerQueryError(field, "Filter must not exceed 100 characters.")
    return normalized


def _normalize_time(value: datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    if value.utcoffset() is None:
        raise InvalidLedgerQueryError(field, "Timestamp must include a UTC offset.")
    return value.astimezone(UTC)


async def search_ledger_events(
    session: AsyncSession,
    *,
    company_id: UUID,
    event_type: str | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    agent_run_id: UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> LedgerEventListResult:
    if not 1 <= limit <= MAX_LEDGER_PAGE_SIZE:
        raise InvalidLedgerQueryError(
            "limit", f"Limit must be between 1 and {MAX_LEDGER_PAGE_SIZE}."
        )
    if not 0 <= offset <= MAX_LEDGER_OFFSET:
        raise InvalidLedgerQueryError(
            "offset", f"Offset must be between 0 and {MAX_LEDGER_OFFSET}."
        )
    normalized_from = _normalize_time(created_from, "created_from")
    normalized_to = _normalize_time(created_to, "created_to")
    if (
        normalized_from is not None
        and normalized_to is not None
        and normalized_from > normalized_to
    ):
        raise InvalidLedgerQueryError(
            "created_to", "created_to must be at or after created_from."
        )

    events, total = await repository.search_ledger_events(
        session,
        company_id=company_id,
        event_type=_normalize_filter(event_type, "event_type"),
        entity_type=_normalize_filter(entity_type, "entity_type"),
        entity_id=entity_id,
        agent_run_id=agent_run_id,
        created_from=normalized_from,
        created_to=normalized_to,
        limit=limit,
        offset=offset,
    )
    return LedgerEventListResult(
        items=[_event_summary(event) for event in events],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_ledger_event_detail(
    session: AsyncSession,
    *,
    company_id: UUID,
    event_id: UUID,
) -> LedgerEventDetail:
    event = await repository.get_ledger_event(
        session,
        company_id=company_id,
        event_id=event_id,
    )
    if event is None:
        raise LedgerEventNotFoundError(str(event_id))

    evidence_rows = await repository.list_bounded_event_evidence(
        session,
        company_id=company_id,
        event_id=event_id,
        limit=MAX_EVENT_EVIDENCE + 1,
    )
    parent_rows = await repository.list_parent_neighbors(
        session,
        company_id=company_id,
        event_id=event_id,
        limit=MAX_EVENT_NEIGHBORS + 1,
    )
    child_rows = await repository.list_child_neighbors(
        session,
        company_id=company_id,
        event_id=event_id,
        limit=MAX_EVENT_NEIGHBORS + 1,
    )

    evidence = [
        LedgerEvidenceSummary(
            id=item.id,
            evidence_type=item.evidence_type,
            locator=item.locator,
            checksum=item.checksum,
            metadata=item.evidence_metadata,
            relevance=link.relevance,
            source_document_id=document.id,
            source_filename=document.filename,
            source_document_checksum=document.checksum,
            data_source_id=data_source.id,
            data_source_name=data_source.name,
            is_synthetic=data_source.is_synthetic,
            created_at=item.created_at,
        )
        for link, item, document, data_source in evidence_rows[:MAX_EVENT_EVIDENCE]
    ]
    parents = [
        LedgerLineageNeighbor(
            edge_id=edge.id,
            relationship_type=edge.relationship_type,
            metadata=edge.edge_metadata,
            created_at=edge.created_at,
            event=_event_summary(neighbor),
        )
        for edge, neighbor in parent_rows[:MAX_EVENT_NEIGHBORS]
    ]
    children = [
        LedgerLineageNeighbor(
            edge_id=edge.id,
            relationship_type=edge.relationship_type,
            metadata=edge.edge_metadata,
            created_at=edge.created_at,
            event=_event_summary(neighbor),
        )
        for edge, neighbor in child_rows[:MAX_EVENT_NEIGHBORS]
    ]
    return LedgerEventDetail(
        **_event_summary(event).model_dump(),
        payload=event.payload,
        evidence=evidence,
        parents=parents,
        children=children,
        evidence_truncated=len(evidence_rows) > MAX_EVENT_EVIDENCE,
        parents_truncated=len(parent_rows) > MAX_EVENT_NEIGHBORS,
        children_truncated=len(child_rows) > MAX_EVENT_NEIGHBORS,
    )


def normalize_json(value: Any) -> Any:
    """Return a deterministic, JSONB-safe representation of an application payload."""
    if isinstance(value, BaseModel):
        return normalize_json(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(key): normalize_json(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [normalize_json(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported ledger payload value: {type(value).__name__}")


def payload_sha256(payload: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    normalized = normalize_json(payload)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return normalized, hashlib.sha256(encoded).hexdigest()


async def append_ledger_event(
    session: AsyncSession,
    *,
    company_id: UUID,
    event_type: str,
    entity_type: str,
    entity_id: UUID,
    payload: Mapping[str, Any],
    analysis_signature: str | None = None,
    created_by: UUID | None = None,
    supersedes_event_id: UUID | None = None,
) -> LedgerEvent:
    """Append one immutable event and flush it inside the caller-owned transaction."""
    normalized_payload, digest = payload_sha256(payload)
    event = LedgerEvent(
        company_id=company_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=normalized_payload,
        payload_hash=digest,
        analysis_signature=analysis_signature,
        created_by=created_by,
        supersedes_event_id=supersedes_event_id,
    )
    session.add(event)
    await session.flush()
    return event


async def append_lineage_edge(
    session: AsyncSession,
    *,
    company_id: UUID,
    parent_event_id: UUID,
    child_event_id: UUID,
    relationship_type: str,
    metadata: Mapping[str, Any] | None = None,
) -> LineageEdge:
    """Append an idempotent directed relationship inside the caller's transaction."""
    existing = await session.scalar(
        select(LineageEdge).where(
            LineageEdge.company_id == company_id,
            LineageEdge.parent_event_id == parent_event_id,
            LineageEdge.child_event_id == child_event_id,
            LineageEdge.relationship_type == relationship_type,
        )
    )
    if existing is not None:
        return existing
    edge = LineageEdge(
        company_id=company_id,
        parent_event_id=parent_event_id,
        child_event_id=child_event_id,
        relationship_type=relationship_type,
        edge_metadata=normalize_json(metadata or {}),
    )
    session.add(edge)
    await session.flush()
    return edge


async def attach_ledger_evidence(
    session: AsyncSession,
    *,
    company_id: UUID,
    ledger_event_id: UUID,
    evidence_item_id: UUID,
    relevance: str | None = None,
) -> LedgerEventEvidence:
    """Attach evidence to an immutable event without duplicating the composite link."""
    existing = await session.scalar(
        select(LedgerEventEvidence).where(
            LedgerEventEvidence.company_id == company_id,
            LedgerEventEvidence.ledger_event_id == ledger_event_id,
            LedgerEventEvidence.evidence_item_id == evidence_item_id,
        )
    )
    if existing is not None:
        return existing
    link = LedgerEventEvidence(
        company_id=company_id,
        ledger_event_id=ledger_event_id,
        evidence_item_id=evidence_item_id,
        relevance=relevance,
    )
    session.add(link)
    await session.flush()
    return link


async def get_measurement_lineage(
    session: AsyncSession,
    *,
    company_id: UUID,
    measurement_id: UUID,
) -> MeasurementLineageResult:
    """Trace a measurement ledger event back to its bounded source ancestry."""
    measurement = await repository.get_measurement(
        session,
        company_id=company_id,
        measurement_id=measurement_id,
    )
    if measurement is None:
        raise MeasurementNotFoundError(str(measurement_id))

    if measurement.ledger_event_id is None:
        return MeasurementLineageResult(
            measurement_id=measurement.id,
            root_event_id=None,
            nodes=[],
            edges=[],
        )

    discovered: set[UUID] = {measurement.ledger_event_id}
    frontier: set[UUID] = {measurement.ledger_event_id}
    lineage_edges = []
    truncated = False

    for _ in range(MAX_LINEAGE_DEPTH):
        parent_edges = await repository.list_parent_edges(
            session,
            company_id=measurement.company_id,
            child_ids=frontier,
        )
        if not parent_edges:
            break
        lineage_edges.extend(parent_edges)
        parents = {edge.parent_event_id for edge in parent_edges} - discovered
        available = MAX_LINEAGE_EVENTS - len(discovered)
        if len(parents) > available:
            parents = set(sorted(parents, key=str)[:available])
            truncated = True
        discovered.update(parents)
        frontier = parents
        if not frontier or len(discovered) >= MAX_LINEAGE_EVENTS:
            truncated = truncated or bool(frontier)
            break
    else:
        truncated = bool(frontier)

    events = await repository.list_events(
        session,
        company_id=measurement.company_id,
        event_ids=discovered,
    )
    nodes = [
        LineageNode(
            id=str(event.id),
            node_type="ledger_event",
            label=event.event_type,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            event_type=event.event_type,
            payload=event.payload,
            payload_hash=event.payload_hash,
            created_at=event.created_at,
            metadata={"analysis_signature": event.analysis_signature},
        )
        for event in sorted(events, key=lambda item: (item.created_at, str(item.id)))
    ]
    edges = [
        LineageEdgeResult(
            id=str(edge.id),
            source=str(edge.parent_event_id),
            target=str(edge.child_event_id),
            relationship_type=edge.relationship_type,
            metadata=edge.edge_metadata,
        )
        for edge in lineage_edges
        if edge.parent_event_id in discovered and edge.child_event_id in discovered
    ]

    evidence_rows = await repository.list_event_evidence(
        session,
        company_id=measurement.company_id,
        event_ids=discovered,
    )
    seen_evidence: set[UUID] = set()
    for link, evidence, document, data_source in evidence_rows:
        if evidence.id not in seen_evidence:
            seen_evidence.add(evidence.id)
            nodes.append(
                LineageNode(
                    id=f"evidence:{evidence.id}",
                    node_type="evidence",
                    label=evidence.evidence_type,
                    entity_type="evidence_item",
                    entity_id=evidence.id,
                    created_at=evidence.created_at,
                    metadata={
                        "locator": evidence.locator,
                        "checksum": evidence.checksum,
                        "source_document_id": str(document.id),
                        "source_filename": document.filename,
                        "data_source_id": str(data_source.id),
                        "data_source_name": data_source.name,
                        "is_synthetic": data_source.is_synthetic,
                    },
                )
            )
        edges.append(
            LineageEdgeResult(
                id=f"evidence:{evidence.id}:{link.ledger_event_id}",
                source=f"evidence:{evidence.id}",
                target=str(link.ledger_event_id),
                relationship_type="supported_by",
                metadata={"relevance": link.relevance},
            )
        )

    return MeasurementLineageResult(
        measurement_id=measurement.id,
        root_event_id=measurement.ledger_event_id,
        nodes=nodes,
        edges=edges,
        truncated=truncated,
    )
