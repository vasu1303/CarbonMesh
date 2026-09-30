from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AuditTimelineItem(BaseModel):
    id: UUID
    timestamp: datetime
    source: Literal["audit", "ledger"]
    action: str
    entity_type: str
    entity_id: UUID
    actor_id: UUID | None = None
    trace_id: str | None = None
    payload_hash: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    direct: bool


class AuditLineageEvent(BaseModel):
    id: UUID
    event_type: str
    entity_type: str
    entity_id: UUID
    payload_hash: str
    created_at: datetime


class AuditLineageEdge(BaseModel):
    id: UUID
    parent_event_id: UUID
    child_event_id: UUID
    relationship_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditLineageSummary(BaseModel):
    events: list[AuditLineageEvent]
    edges: list[AuditLineageEdge]
    truncated: bool = False


class AuditEntityResult(BaseModel):
    entity_type: str
    entity_id: UUID
    company_id: UUID
    timeline: list[AuditTimelineItem]
    lineage: AuditLineageSummary
