from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Workflow = Literal["measurement", "procurement", "cross_module", "unsupported"]
RunState = Literal[
    "running",
    "needs_clarification",
    "no_data",
    "validation_error",
    "unsupported",
    "no_feasible_option",
    "failed_validation",
    "budget_exhausted",
    "approval_invalidated",
    "completed",
    "failed",
]
TerminalState = Literal[
    "needs_clarification",
    "no_data",
    "validation_error",
    "unsupported",
    "no_feasible_option",
    "failed_validation",
    "budget_exhausted",
    "approval_invalidated",
    "completed",
]
ActorRole = Literal[
    "sustainability_analyst",
    "procurement_manager",
    "approver",
    "auditor",
    "system",
]
EventName = Literal[
    "run.started",
    "stage.started",
    "stage.completed",
    "tool.started",
    "tool.completed",
    "validation.warning",
    "fact.created",
    "approval.required",
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


class AgentContextRequest(StrictModel):
    company_id: UUID
    actor_id: UUID
    site_id: UUID | None = None
    reporting_period_id: UUID | None = None
    carbon_measurement_id: UUID | None = None
    current_product_id: UUID | None = None
    method_definition_id: UUID | None = None
    metric_keys: list[MetricKey] = Field(default_factory=list, max_length=20)
    material_scope: list[ScopeValue] = Field(default_factory=list, max_length=20)
    supplier_product_ids: list[UUID] = Field(default_factory=list, max_length=100)
    constraints: AgentConstraints = Field(default_factory=AgentConstraints)

    @field_validator("metric_keys", "material_scope", "supplier_product_ids")
    @classmethod
    def reject_duplicate_scope_values(cls, values: list[object]) -> list[object]:
        if len(values) != len(set(values)):
            raise ValueError("scope values must be unique")
        return values


class AgentQueryRequest(StrictModel):
    query: str = Field(min_length=3, max_length=4000)
    context: AgentContextRequest

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 3:
            raise ValueError("query must contain at least three non-whitespace characters")
        return normalized


class AgentBudget(StrictModel):
    max_model_calls: int = Field(default=3, ge=0, le=3)
    max_tool_calls: int = Field(default=6, ge=0, le=6)
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
    site_id: UUID | None
    reporting_period_id: UUID | None
    carbon_measurement_id: UUID | None = None
    current_product_id: UUID | None = None
    method_definition_id: UUID | None = None
    workflow: Workflow
    metric_keys: list[MetricKey]
    material_scope: list[ScopeValue]
    supplier_product_ids: list[UUID]
    actor_id: UUID
    actor_role: ActorRole
    constraints: AgentConstraints
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
    version: Literal["orchestrator.v1"] = "orchestrator.v1"
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
    basis: Literal["deterministic_keyword_rules"]
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


class AgentEvent(StrictModel):
    sequence: int = Field(ge=1)
    name: EventName
    occurred_at: datetime
    data: dict[str, object] = Field(default_factory=dict)


class StageTelemetry(StrictModel):
    stage: str = Field(min_length=1, max_length=100)
    elapsed_ms: int = Field(ge=0)


class AgentTelemetry(StrictModel):
    trace_id: str = Field(min_length=1, max_length=100)
    analysis_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    orchestrator_version: Literal["orchestrator.v1"] = "orchestrator.v1"
    provider: Literal["none"] = "none"
    model_id: None = None
    model_calls: int = Field(default=0, ge=0, le=3)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    context_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0, le=6)
    retry_count: int = Field(default=0, ge=0, le=1)
    repairs: int = Field(default=0, ge=0, le=1)
    rows_processed: int = Field(default=0, ge=0)
    evidence_chunks_retrieved: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(ge=0)
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
    plan: AgentPlan | None
    facts: list[AgentFact]
    judgments: list[AgentJudgment]
    telemetry: AgentTelemetry
    approval_requirement: ApprovalRequirement
    recommendation: AgentRecommendation | None
    message: str | None
    missing_fields: list[str]
    unsupported_reason: str | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None
