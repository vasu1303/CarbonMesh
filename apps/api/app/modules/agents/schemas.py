from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.modules.agents.fresh_contracts import FreshRunInputs

Workflow = Literal[
    "planning",
    "measurement",
    "assurance",
    "procurement",
    "dispatch",
    "cross_module",
    "four_module",
    "unsupported",
]
RunState = Literal[
    "running",
    "success",
    "needs_clarification",
    "no_data",
    "validation_error",
    "unsupported",
    "policy_blocked",
    "provider_unavailable",
    "no_feasible_option",
    "no_feasible_candidate",
    "no_feasible_window",
    "failed_validation",
    "validation_failed",
    "budget_exhausted",
    "approval_required",
    "approval_invalidated",
    "stale",
    "completed",
    "failed",
]
TerminalState = Literal[
    "success",
    "needs_clarification",
    "no_data",
    "validation_error",
    "unsupported",
    "policy_blocked",
    "provider_unavailable",
    "no_feasible_option",
    "no_feasible_candidate",
    "no_feasible_window",
    "failed_validation",
    "validation_failed",
    "budget_exhausted",
    "approval_required",
    "approval_invalidated",
    "stale",
    "completed",
]
ActorRole = Literal[
    "sustainability_analyst",
    "procurement_manager",
    "approver",
    "auditor",
    "compliance_reviewer",
    "operations_planner",
    "system",
]
EventName = Literal[
    "run.started",
    "node.started",
    "node.completed",
    "node.failed",
    "stage.started",
    "stage.completed",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "provider.started",
    "provider.completed",
    "provider.failed",
    "provider.cache_hit",
    "validation.warning",
    "fact.created",
    "clarification.required",
    "approval.required",
    "run.resumed",
    "run.resume_blocked",
    "run.resume_resolved",
    "run.completed",
    "run.stopped",
]
PlanStepStatus = Literal["completed", "planned", "blocked", "not_applicable"]

MetricKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$",
    ),
]
ScopeValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentConstraints(StrictModel):
    """Hard procurement constraints supplied by the caller; none are inferred."""

    max_cost_increase_pct: Decimal | None = Field(default=None, ge=0, le=100)
    max_lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    minimum_circularity_score: Decimal | None = Field(default=None, ge=0, le=100)


class AgentDispatchConstraints(StrictModel):
    window_start: datetime | None = None
    window_end: datetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=1440)
    max_delay_minutes: int | None = Field(default=None, ge=0, le=10080)
    maximum_power_kw: Decimal | None = Field(default=None, gt=0)
    blackout_constraint_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("window_start", "window_end")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("dispatch window timestamps must include a UTC offset")
        return value

    @model_validator(mode="after")
    def require_ordered_window(self) -> AgentDispatchConstraints:
        if (
            self.window_start is not None
            and self.window_end is not None
            and self.window_end <= self.window_start
        ):
            raise ValueError("dispatch window_end must be after window_start")
        if len(self.blackout_constraint_ids) != len(set(self.blackout_constraint_ids)):
            raise ValueError("dispatch blackout constraint IDs must be unique")
        return self


class AgentContextRequest(StrictModel):
    company_id: UUID
    actor_id: UUID
    grid_source_mode: Literal["live", "fixture"] = "live"
    fresh_inputs: FreshRunInputs | None = None
    site_id: UUID | None = None
    reporting_period_id: UUID | None = None
    carbon_measurement_id: UUID | None = None
    current_product_id: UUID | None = None
    method_definition_id: UUID | None = None
    activity_record_ids: list[UUID] = Field(default_factory=list, max_length=500)
    standard_id: UUID | None = None
    disclosure_draft_id: UUID | None = None
    requirement_ids: list[UUID] = Field(default_factory=list, max_length=100)
    evidence_item_ids: list[UUID] = Field(default_factory=list, max_length=100)
    procurement_scenario_id: UUID | None = None
    flexible_load_id: UUID | None = None
    dispatch_scenario_id: UUID | None = None
    forecast_id: UUID | None = None
    policy_definition_id: UUID | None = None
    metric_keys: list[MetricKey] = Field(default_factory=list, max_length=20)
    material_scope: list[ScopeValue] = Field(default_factory=list, max_length=20)
    supplier_product_ids: list[UUID] = Field(default_factory=list, max_length=100)
    constraints: AgentConstraints = Field(default_factory=AgentConstraints)
    dispatch_constraints: AgentDispatchConstraints = Field(
        default_factory=AgentDispatchConstraints
    )

    @field_validator(
        "activity_record_ids",
        "requirement_ids",
        "evidence_item_ids",
        "metric_keys",
        "material_scope",
        "supplier_product_ids",
    )
    @classmethod
    def reject_duplicate_scope_values(cls, values: list[object]) -> list[object]:
        if len(values) != len(set(values)):
            raise ValueError("scope values must be unique")
        return values


class AgentQueryRequest(StrictModel):
    query: str = Field(min_length=3, max_length=4000)
    context: AgentContextRequest
    previous_run_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 3:
            raise ValueError("query must contain at least three non-whitespace characters")
        return normalized


class AgentBudget(StrictModel):
    max_model_calls: int = Field(default=3, ge=0, le=6)
    max_tool_calls: int = Field(default=8, ge=0, le=20)
    max_repairs: int = Field(default=1, ge=0, le=1)
    max_context_turns: int = Field(default=6, ge=0, le=6)
    max_context_tokens: int = Field(default=4000, ge=0, le=4000)
    target_latency_ms: int = Field(default=15_000, gt=0)


class ResolvedWorkflowContext(StrictModel):
    carbon_measurement_id: UUID
    activity_record_id: UUID
    current_product_id: UUID
    method_definition_id: UUID | None = None
    quantity: Decimal = Field(gt=0)
    quantity_unit: str = Field(min_length=1, max_length=50)
    current_unit_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class FrozenContextEnvelope(StrictModel):
    company_id: UUID
    grid_source_mode: Literal["live", "fixture"] = "live"
    fresh_inputs: FreshRunInputs | None = None
    site_id: UUID | None
    reporting_period_id: UUID | None
    carbon_measurement_id: UUID | None = None
    current_product_id: UUID | None = None
    method_definition_id: UUID | None = None
    activity_record_ids: list[UUID] = Field(default_factory=list)
    standard_id: UUID | None = None
    disclosure_draft_id: UUID | None = None
    requirement_ids: list[UUID] = Field(default_factory=list)
    evidence_item_ids: list[UUID] = Field(default_factory=list)
    procurement_scenario_id: UUID | None = None
    flexible_load_id: UUID | None = None
    dispatch_scenario_id: UUID | None = None
    forecast_id: UUID | None = None
    policy_definition_id: UUID | None = None
    workflow: Workflow
    metric_keys: list[MetricKey]
    material_scope: list[ScopeValue]
    supplier_product_ids: list[UUID]
    actor_id: UUID
    actor_role: ActorRole
    constraints: AgentConstraints
    dispatch_constraints: AgentDispatchConstraints = Field(
        default_factory=AgentDispatchConstraints
    )
    resolved: ResolvedWorkflowContext | None = None
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentPlanStep(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    responsibility: str = Field(min_length=1, max_length=500)
    status: PlanStepStatus
    tool_ids: list[str] = Field(default_factory=list, max_length=6)


class AgentPlan(StrictModel):
    version: Literal["orchestrator.initial"] = "orchestrator.initial"
    execution_mode: Literal["deterministic_services"] = "deterministic_services"
    workflow: Workflow
    steps: list[AgentPlanStep] = Field(max_length=6)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    requires_human_approval: bool


class AgentFact(StrictModel):
    fact_id: UUID
    metric_key: MetricKey
    display_value: str = Field(min_length=1, max_length=200)
    ledger_event_id: UUID


class AgentJudgment(StrictModel):
    kind: Literal["workflow_classification"]
    value: Workflow
    basis: Literal["deterministic_keyword_rules", "structured_model_plan", "policy_gate"]
    matched_terms: list[str] = Field(default_factory=list, max_length=30)


class ApprovalRequirement(StrictModel):
    required: bool = False
    approval_id: UUID | None = None
    recommendation_id: UUID | None = None
    preview_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class AgentRecommendation(StrictModel):
    scenario_id: UUID
    recommendation_id: UUID
    recommended_product_id: UUID
    status: str = Field(min_length=1, max_length=30)
    projected_footprint_kgco2e: Decimal = Field(ge=0)
    avoided_kgco2e: Decimal
    reduction_pct: Decimal
    cost_delta_pct: Decimal
    lead_time_delta_days: int
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    ledger_event_id: UUID


class AgentRunPayload(StrictModel):
    message: str = Field(min_length=1, max_length=1000)
    facts: list[AgentFact] = Field(default_factory=list)
    judgments: list[AgentJudgment] = Field(default_factory=list)
    approval_requirement: ApprovalRequirement = Field(default_factory=ApprovalRequirement)
    recommendation: AgentRecommendation | None = None
    missing_fields: list[str] = Field(default_factory=list, max_length=20)
    unsupported_reason: str | None = Field(default=None, max_length=500)
    unsupported_items: list[str] = Field(default_factory=list, max_length=50)
    pending_interrupt: PendingInterrupt | None = None


class AgentEvent(StrictModel):
    sequence: int = Field(ge=1)
    name: EventName
    occurred_at: datetime
    data: dict[str, object] = Field(default_factory=dict)


class StageTelemetry(StrictModel):
    stage: str = Field(min_length=1, max_length=100)
    elapsed_ms: int = Field(ge=0)


class AgentExecutionLease(StrictModel):
    """Durable, fenced ownership for one background run execution."""

    claim_id: UUID
    claimed_at: datetime
    expires_at: datetime
    attempt: int = Field(ge=1)
    recovered: bool = False

    @field_validator("claimed_at", "expires_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("execution lease timestamps must include a UTC offset")
        return value

    @model_validator(mode="after")
    def require_positive_window(self) -> AgentExecutionLease:
        if self.expires_at <= self.claimed_at:
            raise ValueError("execution lease expiry must be after its claim time")
        return self


class AgentTelemetry(StrictModel):
    trace_id: str = Field(min_length=1, max_length=100)
    analysis_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    orchestrator_version: str = Field(default="orchestrator.initial", max_length=100)
    provider: Literal["none", "openai", "gemini", "anthropic", "openrouter"] = "none"
    provider_status: Literal[
        "not_requested",
        "configured",
        "completed",
        "unavailable",
        "blocked",
    ] = "not_requested"
    model_id: str | None = Field(default=None, max_length=200)
    model_calls: int = Field(default=0, ge=0, le=6)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    context_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0, le=20)
    retry_count: int = Field(default=0, ge=0)
    repairs: int = Field(default=0, ge=0, le=1)
    api_calls: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    external_cache_hits: int = Field(default=0, ge=0)
    rows_processed: int = Field(default=0, ge=0)
    evidence_chunks_retrieved: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(ge=0)
    estimated_energy_wh: Decimal | None = Field(default=None, ge=0)
    estimated_co2e_g: Decimal | None = Field(default=None, ge=0)
    sustainability_method: str | None = Field(default=None, max_length=100)
    sustainability_assumptions: dict[str, str] = Field(default_factory=dict)
    graph_context: dict[str, object] | None = None
    graph_state: dict[str, object] | None = None
    checkpoint: dict[str, object] | None = None
    planning_selection: dict[str, object] | None = None
    resume_kind: Literal["clarification", "approval"] | None = None
    resume_approval: dict[str, object] | None = None
    execution_attempts: int = Field(default=0, ge=0)
    execution_lease: AgentExecutionLease | None = None
    stage_timings: list[StageTelemetry] = Field(default_factory=list, max_length=20)
    events: list[AgentEvent] = Field(default_factory=list, max_length=100)


class AgentQueryAccepted(StrictModel):
    run_id: UUID
    trace_id: str
    terminal_state: Literal["running"] = "running"


class AgentRunResult(StrictModel):
    run_id: UUID
    trace_id: str
    workflow: Workflow
    stage: str
    terminal_state: RunState
    context: FrozenContextEnvelope
    plan: AgentPlan | dict[str, object] | None
    facts: list[AgentFact]
    judgments: list[AgentJudgment]
    telemetry: AgentTelemetry
    approval_requirement: ApprovalRequirement
    recommendation: AgentRecommendation | None
    pending_interrupt: PendingInterrupt | None = None
    message: str | None
    missing_fields: list[str]
    unsupported_reason: str | None
    unsupported_items: list[str] = Field(default_factory=list, max_length=50)
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None


InterruptKind = Literal["clarification", "approval"]
InterruptStatus = Literal["pending", "resumed", "resolved", "stale"]


class PendingInterrupt(StrictModel):
    interrupt_id: UUID
    sequence: int = Field(ge=1)
    kind: InterruptKind
    status: InterruptStatus = "pending"
    analysis_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    approval_context_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    missing_fields: list[str] = Field(default_factory=list, max_length=20)
    approval_id: UUID | None = None
    target_type: str | None = Field(default=None, min_length=1, max_length=100)
    target_id: UUID | None = None
    preview_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_kind_payload(self) -> PendingInterrupt:
        approval_values = (
            self.approval_id,
            self.target_type,
            self.target_id,
            self.preview_hash,
            self.expires_at,
        )
        if self.kind == "approval" and any(value is None for value in approval_values):
            raise ValueError("approval interrupts require an exact target and preview")
        if self.kind == "clarification" and not self.missing_fields:
            raise ValueError("clarification interrupts require missing fields")
        if self.expires_at is not None and self.expires_at.utcoffset() is None:
            raise ValueError("interrupt expiry must include a UTC offset")
        return self


class ClarificationResume(StrictModel):
    context: AgentContextRequest
    clarified_query: str | None = Field(default=None, min_length=3, max_length=4000)

    @field_validator("clarified_query")
    @classmethod
    def normalize_clarified_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if len(normalized) < 3:
            raise ValueError("clarified_query must contain at least three characters")
        return normalized


class ApprovalResume(StrictModel):
    approval_id: UUID
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentResumeRequest(StrictModel):
    company_id: UUID
    actor_id: UUID
    interrupt_id: UUID
    interrupt_sequence: int = Field(ge=1)
    analysis_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=255)
    clarification: ClarificationResume | None = None
    approval: ApprovalResume | None = None

    @model_validator(mode="after")
    def require_exact_interrupt_payload(self) -> AgentResumeRequest:
        if (self.clarification is None) == (self.approval is None):
            raise ValueError("exactly one clarification or approval payload is required")
        return self


class AgentResumeResult(StrictModel):
    run_id: UUID
    trace_id: str
    terminal_state: RunState
    resumed: bool
    idempotent_replay: bool = False


class SustainabilityAssumptions(StrictModel):
    method: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    energy_wh_per_1k_tokens: Decimal = Field(ge=0)
    grid_intensity_gco2e_per_kwh: Decimal = Field(ge=0)
    caveat: str = Field(min_length=1, max_length=500)
    functional_unit: str = "Selected agent executions, excluding human review time"
    region_basis: str = "Configured grid-intensity assumption; provider region is not measured"
    omitted_footprint: str = "Embodied hardware, network, storage, and non-model compute"
    uncertainty: str = "Uncalibrated token proxy; no measured provider-energy uncertainty interval"
    benefit_basis: str = "Approved projected recommendations; not measured realized savings"


class SustainabilityBenefitFact(StrictModel):
    target_type: Literal["procurement_recommendation", "dispatch_recommendation"]
    target_id: UUID
    ledger_event_id: UUID
    avoided_kgco2e: Decimal = Field(ge=0)


class AgentSustainabilityMetrics(StrictModel):
    company_id: UUID
    from_time: datetime | None = None
    to_time: datetime | None = None
    run_count: int = Field(ge=0)
    completed_run_count: int = Field(ge=0)
    interrupted_run_count: int = Field(ge=0)
    provider_call_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    estimated_energy_wh: Decimal = Field(ge=0)
    estimated_co2e_g: Decimal = Field(ge=0)
    proxy_coverage_runs: int = Field(ge=0)
    assumptions: SustainabilityAssumptions
    external_api_calls: int = Field(default=0, ge=0)
    business_benefit_kgco2e: Decimal = Field(default=Decimal(0), ge=0)
    benefit_to_footprint_ratio: Decimal | None = Field(default=None, ge=0)
    benefit_facts: list[SustainabilityBenefitFact] = Field(default_factory=list)
    assumption_coverage_runs: int = Field(default=0, ge=0)
    assumption_sets: list[SustainabilityAssumptions] = Field(default_factory=list)
