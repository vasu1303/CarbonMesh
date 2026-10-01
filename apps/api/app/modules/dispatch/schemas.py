from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class DispatchSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class BlackoutWindow(DispatchSchema):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Datetime values must include a UTC offset.")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def ordered(self) -> BlackoutWindow:
        if self.end <= self.start:
            raise ValueError("A blackout end must be later than its start.")
        return self


class CapacityWindow(DispatchSchema):
    available_capacity_kw: Decimal = Field(ge=0)
    start: datetime | None = None
    end: datetime | None = None

    @field_validator("start", "end")
    @classmethod
    def aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Datetime values must include a UTC offset.")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def paired_and_ordered(self) -> CapacityWindow:
        if (self.start is None) != (self.end is None):
            raise ValueError("Capacity-window start and end must be supplied together.")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("A capacity-window end must be later than its start.")
        return self


class OperatingConstraintView(DispatchSchema):
    id: UUID
    code: str
    name: str
    constraint_type: str
    is_hard: bool
    valid_from: datetime | None
    valid_to: datetime | None
    configuration: dict[str, Any]
    is_active: bool


class FlexibleLoadView(DispatchSchema):
    id: UUID
    company_id: UUID
    site_id: UUID
    semantic_entity_id: UUID | None
    code: str
    name: str
    description: str | None
    power_kw: Decimal
    duration_minutes: int
    energy_kwh: Decimal
    minimum_power_kw: Decimal | None
    maximum_power_kw: Decimal | None
    is_interruptible: bool
    metadata: dict[str, Any]
    is_active: bool
    constraints: list[OperatingConstraintView]
    created_at: datetime
    updated_at: datetime


class FlexibleLoadList(DispatchSchema):
    items: list[FlexibleLoadView]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ForecastSyncRequest(DispatchSchema):
    company_id: UUID
    site_id: UUID
    zone: str | None = Field(default=None, min_length=2, max_length=100)
    horizon_hours: Literal[24] = 24
    source_mode: Literal["fixture", "live"] = "fixture"
    force_refresh: bool = False

    @field_validator("zone")
    @classmethod
    def normalize_zone(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None


class ForecastPointView(DispatchSchema):
    id: UUID
    forecast_for: datetime
    intensity_gco2e_per_kwh: Decimal
    is_estimated: bool
    point_hash: Sha256
    evidence_item_id: UUID | None


class ForecastSyncResult(DispatchSchema):
    site_id: UUID
    zone: str
    provider: str
    source_mode: Literal["fixture", "live"]
    synthetic: bool
    issued_at: datetime
    temporal_granularity: Literal["hourly"]
    source_document_id: UUID
    snapshot_hash: Sha256
    received_points: int
    inserted_points: int
    existing_points: int
    estimated_points: int
    forecast_start: datetime
    forecast_end: datetime
    points: list[ForecastPointView]


class CreateDispatchScenarioRequest(DispatchSchema):
    company_id: UUID
    site_id: UUID
    flexible_load_id: UUID
    method_definition_id: UUID
    forecast_source_document_id: UUID
    requested_by: UUID
    policy_definition_id: UUID | None = None
    agent_run_id: UUID | None = None
    window_start: datetime
    window_end: datetime
    baseline_start: datetime
    maximum_delay_minutes: int = Field(default=240, ge=0, le=1440, strict=True)
    available_capacity_kw: Decimal | None = Field(default=None, ge=0)
    blackout_windows: list[BlackoutWindow] = Field(default_factory=list, max_length=100)
    objective: Literal["minimum_carbon"] = "minimum_carbon"
    approval_expires_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("window_start", "window_end", "baseline_start", "approval_expires_at")
    @classmethod
    def aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Datetime values must include a UTC offset.")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def valid_window(self) -> CreateDispatchScenarioRequest:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be later than window_start")
        if self.window_end - self.window_start > timedelta(hours=24):
            raise ValueError("A Dispatch scenario cannot exceed the 24-hour forecast horizon.")
        if not self.window_start <= self.baseline_start < self.window_end:
            raise ValueError("baseline_start must be inside the allowed window")
        if self.approval_expires_at is not None and self.approval_expires_at <= datetime.now(UTC):
            raise ValueError("approval_expires_at must be in the future")
        return self


class FrozenLoadSnapshot(DispatchSchema):
    id: UUID
    code: str
    name: str
    power_kw: Decimal
    duration_minutes: int
    energy_kwh: Decimal
    minimum_power_kw: Decimal | None
    maximum_power_kw: Decimal | None
    is_interruptible: bool
    is_active: bool
    snapshot_hash: Sha256


class FrozenMethodSnapshot(DispatchSchema):
    id: UUID
    key: str
    version: str
    code_version: str
    configuration: dict[str, Any]
    is_active: bool
    snapshot_hash: Sha256


class FrozenPolicySnapshot(DispatchSchema):
    id: UUID
    key: str
    version: str
    configuration: dict[str, Any]
    is_active: bool
    snapshot_hash: Sha256


class FrozenDispatchConstraints(DispatchSchema):
    earliest_start: datetime
    latest_finish: datetime
    baseline_start: datetime
    maximum_delay_minutes: int = Field(ge=0)
    blackouts: list[BlackoutWindow]
    capacity_windows: list[CapacityWindow]
    source_constraints: list[OperatingConstraintView]
    load_snapshot: FrozenLoadSnapshot | None = None
    method_snapshot: FrozenMethodSnapshot | None = None
    policy_snapshot: FrozenPolicySnapshot | None = None
    requested_by: UUID
    approval_expires_at: datetime
    approval_expiry_is_default: bool = True
    approval_idempotency_key: str


class DispatchMethodView(DispatchSchema):
    id: UUID
    key: str
    version: str
    code_version: str


class DispatchScenarioView(DispatchSchema):
    id: UUID
    company_id: UUID
    site_id: UUID
    flexible_load: FlexibleLoadView
    agent_run_id: UUID | None
    method: DispatchMethodView
    policy_definition_id: UUID | None
    forecast_source_document_id: UUID
    window_start: datetime
    window_end: datetime
    objective: str
    constraints: FrozenDispatchConstraints
    forecast_snapshot: list[dict[str, Any]]
    analysis_signature: Sha256
    context_hash: Sha256
    status: str
    created_at: datetime
    updated_at: datetime


class OptimizeScenarioRequest(DispatchSchema):
    company_id: UUID


class RejectedWindowView(DispatchSchema):
    start: datetime
    end: datetime
    reasons: list[str]


class ApprovalPreviewView(DispatchSchema):
    id: UUID
    target_type: Literal["dispatch_recommendation"]
    target_id: UUID
    status: str
    preview_payload: dict[str, Any]
    preview_hash: Sha256
    analysis_signature: Sha256
    context_hash: Sha256
    idempotency_key: str
    expires_at: datetime


class DispatchRecommendationView(DispatchSchema):
    id: UUID
    company_id: UUID
    scenario_id: UUID
    status: str
    recommended_start: datetime
    recommended_end: datetime
    baseline_start: datetime
    baseline_end: datetime
    expected_emissions_kgco2e: Decimal
    baseline_emissions_kgco2e: Decimal
    avoided_kgco2e: Decimal
    reduction_pct: Decimal
    rationale: str
    impact_snapshot: dict[str, Any]
    analysis_signature: Sha256
    payload_hash: Sha256
    ledger_event_id: UUID
    evidence_item_ids: list[UUID]
    approval: ApprovalPreviewView
    actuation_authorized: Literal[False]
    invalidated_at: datetime | None
    created_at: datetime


class DispatchOptimizationResult(DispatchSchema):
    scenario_id: UUID
    terminal_state: Literal["approval_required", "no_feasible_option"]
    evaluated_windows: int = Field(ge=0)
    feasible_windows: int = Field(ge=0)
    rejected_windows: list[RejectedWindowView]
    recommendation: DispatchRecommendationView | None


class DispatchRecommendationResult(DispatchSchema):
    scenario_id: UUID
    terminal_state: Literal["approval_required", "no_feasible_option"]
    recommendation: DispatchRecommendationView | None
