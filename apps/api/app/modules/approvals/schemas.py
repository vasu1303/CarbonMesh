from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ApprovalStatus = Literal["pending", "approved", "rejected"]


class ApprovalItem(BaseModel):
    id: UUID
    company_id: UUID
    recommendation_id: UUID
    status: ApprovalStatus
    preview_hash: str
    analysis_signature: str
    expires_at: datetime
    created_at: datetime
    requested_by: UUID
    requester_name: str
    decided_by: UUID | None = None
    decider_name: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    ledger_event_id: UUID | None = None
    recommended_product_id: UUID
    recommended_product_name: str
    supplier_name: str
    projected_footprint_kgco2e: Decimal
    avoided_kgco2e: Decimal
    reduction_pct: Decimal
    cost_delta_pct: Decimal
    lead_time_delta_days: int
    expired: bool
    preview_current: bool


class ApprovalListResult(BaseModel):
    items: list[ApprovalItem]
    total: int
    limit: int
    offset: int


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    decision: Literal["approve", "reject"]
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_id: UUID
    decision_note: str | None = Field(default=None, max_length=2000)


class ApprovalDecisionResult(BaseModel):
    approval_id: UUID
    recommendation_id: UUID
    status: Literal["approved", "rejected"]
    preview_hash: str
    analysis_signature: str
    decided_by: UUID
    decided_at: datetime
    decision_note: str | None
    ledger_event_id: UUID
    idempotent_replay: bool = False
