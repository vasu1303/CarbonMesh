from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LineageNode(BaseModel):
    """React Flow compatible node backed by a ledger event or evidence item."""

    id: str
    node_type: Literal["ledger_event", "evidence"]
    label: str
    entity_type: str | None = None
    entity_id: UUID | None = None
    event_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_hash: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LineageEdgeResult(BaseModel):
    """Directed source-to-output edge suitable for React Flow."""

    id: str
    source: str
    target: str
    relationship_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MeasurementLineageResult(BaseModel):
    measurement_id: UUID
    root_event_id: UUID | None
    nodes: list[LineageNode]
    edges: list[LineageEdgeResult]
    truncated: bool = False


class LedgerEventSummary(BaseModel):
    """Bounded event metadata returned by searches and neighbor projections."""

    id: UUID
    company_id: UUID
    event_type: str
    entity_type: str
    entity_id: UUID
    payload_hash: str
    analysis_signature: str | None = None
    created_by: UUID | None = None
    supersedes_event_id: UUID | None = None
    created_at: datetime


class LedgerEventListResult(BaseModel):
    items: list[LedgerEventSummary]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)


class LedgerEvidenceSummary(BaseModel):
    """Evidence metadata only; source and evidence bodies are intentionally excluded."""

    id: UUID
    evidence_type: str
    locator: str
    checksum: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    relevance: str | None = None
    source_document_id: UUID
    source_filename: str
    source_document_checksum: str
    data_source_id: UUID
    data_source_name: str
    is_synthetic: bool
    created_at: datetime


class LedgerLineageNeighbor(BaseModel):
    edge_id: UUID
    relationship_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    event: LedgerEventSummary


class LedgerEventDetail(LedgerEventSummary):
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: list[LedgerEvidenceSummary] = Field(default_factory=list)
    parents: list[LedgerLineageNeighbor] = Field(default_factory=list)
    children: list[LedgerLineageNeighbor] = Field(default_factory=list)
    evidence_truncated: bool = False
    parents_truncated: bool = False
    children_truncated: bool = False
