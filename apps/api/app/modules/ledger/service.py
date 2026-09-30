from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ledger import LedgerEvent, LedgerEventEvidence, LineageEdge
from app.modules.ledger import repository
from app.modules.ledger.schemas import (
    LineageEdgeResult,
    LineageNode,
    MeasurementLineageResult,
)

MAX_LINEAGE_DEPTH = 25
MAX_LINEAGE_EVENTS = 200


class MeasurementNotFoundError(LookupError):
    """Raised when a measurement does not exist."""


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
