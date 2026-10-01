from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

ApprovalStatus = Literal["pending", "approved", "rejected"]
GenericApprovalStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "invalidated",
    "expired",
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
TargetType = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
IdempotencyKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class GenericApprovalPreviewRequest(BaseModel):
    """Exact generic preview input owned by the calling domain transaction."""

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    target_type: TargetType
    target_id: UUID
    requested_by: UUID
    preview_payload: dict[str, Any] = Field(min_length=1)
    preview_hash: Sha256
    analysis_signature: Sha256
    context_hash: Sha256
    idempotency_key: IdempotencyKey | None = None
    policy_definition_id: UUID | None = None
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("expires_at must include a UTC offset")
        return value


class GenericApprovalPreviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    company_id: UUID
    target_type: TargetType
    target_id: UUID
    requested_by: UUID
    status: GenericApprovalStatus
    preview_payload: dict[str, Any]
    preview_hash: Sha256
    analysis_signature: Sha256
    context_hash: Sha256
    idempotency_key: IdempotencyKey
    policy_definition_id: UUID | None
    expires_at: datetime
    idempotent_replay: bool = False


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
