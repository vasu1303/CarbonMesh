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
