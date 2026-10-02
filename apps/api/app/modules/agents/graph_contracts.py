"""Strict contracts shared by the bounded CarbonMesh LangGraph runtime.

The models in this module deliberately contain no database or domain-service
objects.  A graph can carry identifiers, immutable context, verified fact
references, and safe tool summaries, but calculations and persistence remain
behind the typed tool boundary.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Final, Literal, Protocol, get_args
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

ModuleName = Literal["measurement", "assurance", "procurement", "dispatch"]
GraphName = Literal[
    "orchestrator",
    "measurement",
    "assurance",
    "procurement",
    "dispatch",
]
BudgetProfileName = Literal["single", "golden", "resume"]
GraphStatus = Literal["ready", "running", "success", "stopped", "interrupted"]
TerminalState = Literal[
    "success",
    "needs_clarification",
    "no_data",
    "unsupported",
    "policy_blocked",
    "provider_unavailable",
    "budget_exhausted",
    "validation_failed",
    "approval_required",
    "no_feasible_option",
    "stale",
]
ToolResultStatus = TerminalState
TransitionKind = Literal[
    "context",
    "planner",
    "policy",
    "graph",
    "node",
    "tool",
    "validation",
    "interrupt",
    "approval",
    "finalize",
]
TransitionStatus = Literal[
    "started",
    "completed",
    "skipped",
    "failed",
    "blocked",
    "interrupted",
]

ToolName = Literal[
    "resolve_context",
    "resolve_entity",
    "retrieve_ledger_facts",
    "retrieve_evidence",
    "write_ledger_event",
    "create_approval_preview",
    "validate_activity",
    "normalize_unit",
    "sync_grid_history",
    "select_emission_factor",
    "calculate_emissions",
    "calculate_confidence",
    "map_standard_requirement",
    "decompose_claim",
    "bind_claim_facts",
    "validate_citations",
    "detect_evidence_gaps",
    "load_supplier_candidates",
    "score_supplier",
    "calculate_procurement_impact",
    "build_procurement_recommendation",
    "sync_grid_forecast",
    "optimize_dispatch_window",
    "calculate_dispatch_impact",
]

TOOL_NAMES: Final[tuple[ToolName, ...]] = get_args(ToolName)

MODULE_ORDER: Final[tuple[ModuleName, ...]] = (
    "measurement",
    "assurance",
    "procurement",
    "dispatch",
)

CONTEXT_TOOL_ORDER: Final[tuple[ToolName, ...]] = (
    "resolve_context",
    "resolve_entity",
)

_MODULE_TOOL_ORDER: dict[ModuleName, tuple[ToolName, ...]] = {
    "measurement": (
        "validate_activity",
        "normalize_unit",
        "sync_grid_history",
        "select_emission_factor",
        "calculate_emissions",
        "calculate_confidence",
        "write_ledger_event",
    ),
    "assurance": (
        "map_standard_requirement",
        "decompose_claim",
        "retrieve_ledger_facts",
        "retrieve_evidence",
        "bind_claim_facts",
        "validate_citations",
        "detect_evidence_gaps",
        "write_ledger_event",
        "create_approval_preview",
    ),
    "procurement": (
        "retrieve_ledger_facts",
        "load_supplier_candidates",
        "score_supplier",
        "calculate_procurement_impact",
        "build_procurement_recommendation",
        "write_ledger_event",
        "create_approval_preview",
    ),
    "dispatch": (
        "sync_grid_forecast",
        "optimize_dispatch_window",
        "calculate_dispatch_impact",
        "write_ledger_event",
        "create_approval_preview",
    ),
}
MODULE_TOOL_ORDER: Final = MappingProxyType(_MODULE_TOOL_ORDER)

_MODULE_DEPENDENCIES: dict[ModuleName, tuple[ModuleName, ...]] = {
    "measurement": (),
    "assurance": ("measurement",),
    "procurement": ("measurement",),
    "dispatch": ("measurement",),
}
MODULE_DEPENDENCIES: Final = MappingProxyType(_MODULE_DEPENDENCIES)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=150,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    ),
]
type JsonScalar = str | int | bool | Decimal | UUID | datetime | None
type ToolValue = JsonScalar | tuple[JsonScalar, ...]


class FrozenContract(BaseModel):
    """Base for persisted graph values that must not be mutated in-place."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
    )


class ContextEntity(FrozenContract):
    entity_type: Identifier
    entity_id: UUID


class ContextConstraint(FrozenContract):
    """One canonical hard constraint value; graph nodes cannot relax it."""

    key: Identifier
    value: str = Field(min_length=1, max_length=500)
    hard: Literal[True] = True


class ImmutableContextEnvelope(FrozenContract):
    company_id: UUID
    fresh_inputs: FreshRunInputs | None = None
    site_id: UUID | None = None
    reporting_period_id: UUID | None = None
    actor_id: UUID
    actor_role: Identifier
    modules: tuple[ModuleName, ...] = Field(min_length=1, max_length=4)
    metric_keys: tuple[Identifier, ...] = Field(default=(), max_length=32)
    material_scope: tuple[str, ...] = Field(default=(), max_length=100)
    entities: tuple[ContextEntity, ...] = Field(default=(), max_length=100)
    method_definition_ids: tuple[UUID, ...] = Field(default=(), max_length=32)
    constraints: tuple[ContextConstraint, ...] = Field(default=(), max_length=100)
    request_hash: Sha256
    analysis_signature: Sha256

    @model_validator(mode="after")
    def validate_immutable_scope(self) -> ImmutableContextEnvelope:
        _require_unique(self.modules, "context modules")
        _require_canonical_modules(self.modules, "context modules")
        _require_unique(self.metric_keys, "metric keys")
        _require_unique(self.material_scope, "material scope")
        _require_unique(self.method_definition_ids, "method definition IDs")
        _require_unique(
            tuple((entity.entity_type, entity.entity_id) for entity in self.entities),
            "context entities",
        )
        _require_unique(tuple(item.key for item in self.constraints), "constraint keys")
        return self


# The shorter name is the public graph-boundary term used by the architecture.
ContextEnvelope = ImmutableContextEnvelope


class BudgetLimits(FrozenContract):
    max_model_calls: int = Field(ge=0, le=6)
    max_tool_calls: int = Field(ge=0, le=20)
    max_repairs_per_stage: int = Field(ge=0, le=1)
    target_latency_ms: int = Field(gt=0, le=120_000)


SINGLE_BUDGET: Final = BudgetLimits(
    max_model_calls=3,
    max_tool_calls=8,
    max_repairs_per_stage=1,
    target_latency_ms=15_000,
)
GOLDEN_BUDGET: Final = BudgetLimits(
    max_model_calls=6,
    max_tool_calls=20,
    max_repairs_per_stage=1,
    target_latency_ms=45_000,
)
RESUME_BUDGET: Final = BudgetLimits(
    max_model_calls=1,
    max_tool_calls=3,
    max_repairs_per_stage=0,
    target_latency_ms=10_000,
)
BUDGET_PROFILES: Final = MappingProxyType(
    {
        "single": SINGLE_BUDGET,
        "golden": GOLDEN_BUDGET,
        "resume": RESUME_BUDGET,
    }
)


def budget_limits_for(profile: BudgetProfileName) -> BudgetLimits:
    return BUDGET_PROFILES[profile]


class StageRepairCount(FrozenContract):
    stage_id: Identifier
    count: int = Field(ge=1)


class BudgetExhaustedError(RuntimeError):
    """Raised before a call would exceed its approved runtime budget."""

    def __init__(self, counter: str, stage_id: str | None = None) -> None:
        detail = f" for stage {stage_id}" if stage_id else ""
        super().__init__(f"{counter} budget exhausted{detail}")
        self.counter = counter
        self.stage_id = stage_id


class BudgetState(FrozenContract):
    profile: BudgetProfileName
    limits: BudgetLimits
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    repairs_by_stage: tuple[StageRepairCount, ...] = ()
    elapsed_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_budget_state(self) -> BudgetState:
        if self.limits != budget_limits_for(self.profile):
            raise ValueError("budget limits must match the approved profile")
        if self.model_calls > self.limits.max_model_calls:
            raise ValueError("model call usage exceeds the approved budget")
        if self.tool_calls > self.limits.max_tool_calls:
            raise ValueError("tool call usage exceeds the approved budget")
        _require_unique(
            tuple(item.stage_id for item in self.repairs_by_stage),
            "repair stage IDs",
        )
        if any(item.count > self.limits.max_repairs_per_stage for item in self.repairs_by_stage):
            raise ValueError("repair usage exceeds the approved per-stage budget")
        return self

    @classmethod
    def for_profile(cls, profile: BudgetProfileName) -> BudgetState:
        return cls(profile=profile, limits=budget_limits_for(profile))

    def consume_tool_call(self) -> BudgetState:
        if self.tool_calls >= self.limits.max_tool_calls:
            raise BudgetExhaustedError("tool-call")
        return self.model_copy(update={"tool_calls": self.tool_calls + 1})

    def consume_repair(self, stage_id: str) -> BudgetState:
        current = {item.stage_id: item.count for item in self.repairs_by_stage}
        count = current.get(stage_id, 0)
        if count >= self.limits.max_repairs_per_stage:
            raise BudgetExhaustedError("repair", stage_id)
        current[stage_id] = count + 1
        repairs = tuple(
            StageRepairCount(stage_id=key, count=value)
            for key, value in sorted(current.items())
        )
        return self.model_copy(update={"repairs_by_stage": repairs})

class PlanStage(FrozenContract):
    stage_id: Identifier
    module: ModuleName
    depends_on: tuple[ModuleName, ...] = ()
    tool_names: tuple[ToolName, ...] = ()
    requires_human_approval: bool = False

    @model_validator(mode="after")
    def validate_stage(self) -> PlanStage:
        _require_unique(self.depends_on, f"{self.module} dependencies")
        _require_canonical_modules(self.depends_on, f"{self.module} dependencies")
        _require_unique(self.tool_names, f"{self.module} tools")
        allowed = MODULE_TOOL_ORDER[self.module]
        invalid = set(self.tool_names) - set(allowed)
        if invalid:
            raise ValueError(
                f"{self.module} plan contains tools outside its allowlist: "
                + ", ".join(sorted(invalid))
            )
        positions = tuple(allowed.index(tool_name) for tool_name in self.tool_names)
        if positions != tuple(sorted(positions)):
            raise ValueError(f"{self.module} tools must follow the approved dependency order")
        if "create_approval_preview" in self.tool_names and not self.requires_human_approval:
            raise ValueError("approval preview tools require a human-approval plan stage")
        if self.requires_human_approval and "create_approval_preview" not in self.tool_names:
            raise ValueError("human-approval plan stages require an approval preview tool")
        return self


class ExecutionPlan(FrozenContract):
    version: Literal["carbonmesh.execution-plan.v1"] = "carbonmesh.execution-plan.v1"
    profile: BudgetProfileName
    context_tools: tuple[ToolName, ...] = ("resolve_context",)
    stages: tuple[PlanStage, ...] = Field(min_length=1, max_length=4)
    expected_model_calls: int = Field(default=0, ge=0, le=6)

    @model_validator(mode="after")
    def validate_plan(self) -> ExecutionPlan:
        _require_unique(self.context_tools, "context tools")
        invalid_context_tools = set(self.context_tools) - set(CONTEXT_TOOL_ORDER)
        if invalid_context_tools:
            raise ValueError("context plan contains a non-context tool")
        context_positions = tuple(
            CONTEXT_TOOL_ORDER.index(tool_name) for tool_name in self.context_tools
        )
        if context_positions != tuple(sorted(context_positions)):
            raise ValueError("context tools must follow the approved dependency order")

        modules = tuple(stage.module for stage in self.stages)
        _require_unique(modules, "plan modules")
        _require_unique(tuple(stage.stage_id for stage in self.stages), "plan stage IDs")
        _require_canonical_modules(modules, "plan modules")
        if self.profile == "single" and len(modules) != 1:
            raise ValueError("the single-module profile requires exactly one module")
        if self.profile == "golden" and len(modules) < 2:
            raise ValueError("the golden profile requires at least two modules")
        if self.profile == "resume" and len(modules) != 1:
            raise ValueError("the resume profile targets exactly one interrupted module")

        previous: tuple[ModuleName, ...] = ()
        for stage in self.stages:
            if any(dependency not in previous for dependency in stage.depends_on):
                raise ValueError("plan dependencies must name earlier stages")
            required_if_present = tuple(
                dependency
                for dependency in MODULE_DEPENDENCIES[stage.module]
                if dependency in modules
            )
            if any(dependency not in stage.depends_on for dependency in required_if_present):
                raise ValueError(
                    f"{stage.module} must depend on earlier included prerequisite modules"
                )
            previous += (stage.module,)

        limits = budget_limits_for(self.profile)
        planned_tools = len(self.context_tools) + sum(
            len(stage.tool_names) for stage in self.stages
        )
        if planned_tools > limits.max_tool_calls:
            raise ValueError("execution plan exceeds its tool-call budget")
        if self.expected_model_calls > limits.max_model_calls:
            raise ValueError("execution plan exceeds its model-call budget")
        return self

    @property
    def modules(self) -> tuple[ModuleName, ...]:
        return tuple(stage.module for stage in self.stages)

    def stage_for(self, module: ModuleName) -> PlanStage | None:
        return next((stage for stage in self.stages if stage.module == module), None)

    @property
    def plan_hash(self) -> str:
        return _sha256_model(self)


class UnsupportedItem(FrozenContract):
    code: Identifier
    reason: str = Field(min_length=1, max_length=1000)
    module: ModuleName | None = None
    subject_ref: str | None = Field(default=None, max_length=255)
    source_ids: tuple[UUID, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def unique_sources(self) -> UnsupportedItem:
        _require_unique(self.source_ids, "unsupported-item source IDs")
        return self


class ClarificationInterrupt(FrozenContract):
    kind: Literal["clarification"] = "clarification"
    code: Identifier
    message: str = Field(min_length=1, max_length=1000)
    required_fields: tuple[Identifier, ...] = Field(min_length=1, max_length=32)
    context_hash: Sha256

    @model_validator(mode="after")
    def unique_fields(self) -> ClarificationInterrupt:
        _require_unique(self.required_fields, "clarification fields")
        return self


class ApprovalInterrupt(FrozenContract):
    kind: Literal["approval"] = "approval"
    approval_id: UUID
    target_type: Identifier
    target_id: UUID
    preview_hash: Sha256
    analysis_signature: Sha256
    context_hash: Sha256
    expires_at: datetime
    approval_context_hash: Sha256 | None = None

    @field_validator("expires_at")
    @classmethod
    def require_aware_expiry(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("approval interrupt expiry must include a UTC offset")
        return value


PendingInterrupt = Annotated[
    ClarificationInterrupt | ApprovalInterrupt,
    Field(discriminator="kind"),
]


class VerifiedFactReference(FrozenContract):
    fact_id: UUID
    metric_key: Identifier
    ledger_event_id: UUID
    display_value: str = Field(min_length=1, max_length=255)


class ToolOutput(FrozenContract):
    key: Identifier
    value: ToolValue


class ToolInvocation(FrozenContract):
    run_id: UUID
    graph_name: GraphName
    module: ModuleName | None = None
    stage_id: Identifier
    tool_name: ToolName
    context: ContextEnvelope
    prior_outputs: tuple[ToolOutput, ...] = Field(default=(), max_length=200)
    checkpoint_revision: int = Field(ge=0)


class ToolResult(FrozenContract):
    status: ToolResultStatus = "success"
    outputs: tuple[ToolOutput, ...] = Field(default=(), max_length=100)
    facts: tuple[VerifiedFactReference, ...] = Field(default=(), max_length=100)
    unsupported_items: tuple[UnsupportedItem, ...] = Field(default=(), max_length=100)
    pending_interrupt: PendingInterrupt | None = None
    rows_processed: int = Field(default=0, ge=0)
    evidence_chunks_retrieved: int = Field(default=0, ge=0)
    cache_hit: bool = False
    code: Identifier | None = None

    @model_validator(mode="after")
    def validate_result_state(self) -> ToolResult:
        if self.status == "needs_clarification":
            if not isinstance(self.pending_interrupt, ClarificationInterrupt):
                raise ValueError("clarification results require a clarification interrupt")
        elif self.status == "approval_required":
            if not isinstance(self.pending_interrupt, ApprovalInterrupt):
                raise ValueError("approval results require an approval interrupt")
        elif self.pending_interrupt is not None:
            raise ValueError("only interrupt results may carry a pending interrupt")
        return self


class ToolInvoker(Protocol):
    """Only side-effecting dependency visible to graph nodes."""

    async def invoke(self, invocation: ToolInvocation) -> ToolResult: ...


class ToolInvokerError(RuntimeError):
    """Safe typed failure raised by a tool adapter before it can return a result."""

    def __init__(
        self,
        *,
        terminal_state: TerminalState,
        code: str,
    ) -> None:
        if terminal_state in {"success", "needs_clarification", "approval_required"}:
            raise ValueError("tool failures require a non-success terminal state")
        super().__init__(code)
        self.terminal_state = terminal_state
        self.code = code


class ToolCallRecord(FrozenContract):
    graph_name: GraphName
    module: ModuleName | None = None
    stage_id: Identifier
    tool_name: ToolName
    status: ToolResultStatus
    outputs: tuple[ToolOutput, ...] = ()
    rows_processed: int = Field(default=0, ge=0)
    evidence_chunks_retrieved: int = Field(default=0, ge=0)
    cache_hit: bool = False
    code: Identifier | None = None


class ModuleOutcome(FrozenContract):
    module: ModuleName
    terminal_state: TerminalState
    code: Identifier | None = None


class GraphTransition(FrozenContract):
    sequence: int = Field(ge=1)
    graph_name: GraphName
    node_name: Identifier
    kind: TransitionKind
    status: TransitionStatus
    module: ModuleName | None = None
    tool_name: ToolName | None = None
    terminal_state: TerminalState | None = None
    code: Identifier | None = None


class GraphCheckpoint(FrozenContract):
    version: Literal["carbonmesh.graph-checkpoint.v1"] = "carbonmesh.graph-checkpoint.v1"
    revision: int = Field(default=0, ge=0)
    context_hash: Sha256
    plan_hash: Sha256
    completed_nodes: tuple[Identifier, ...] = ()
    completed_modules: tuple[ModuleName, ...] = ()
    next_module: ModuleName | None = None
    last_transition_sequence: int = Field(default=0, ge=0)
    pending_interrupt: PendingInterrupt | None = None

    @model_validator(mode="after")
    def validate_checkpoint(self) -> GraphCheckpoint:
        _require_unique(self.completed_nodes, "completed graph nodes")
        _require_unique(self.completed_modules, "completed checkpoint modules")
        _require_canonical_modules(self.completed_modules, "completed checkpoint modules")
        return self


class AgentGraphState(FrozenContract):
    """Serializable state shared by the orchestrator and all specialist graphs."""

    run_id: UUID
    context: ContextEnvelope
    plan: ExecutionPlan
    checkpoint: GraphCheckpoint
    budget: BudgetState
    status: GraphStatus = "ready"
    terminal_state: TerminalState | None = None
    active_module: ModuleName | None = None
    transitions: tuple[GraphTransition, ...] = Field(default=(), max_length=500)
    tool_records: tuple[ToolCallRecord, ...] = Field(default=(), max_length=100)
    module_outcomes: tuple[ModuleOutcome, ...] = Field(default=(), max_length=4)
    facts: tuple[VerifiedFactReference, ...] = Field(default=(), max_length=200)
    unsupported_items: tuple[UnsupportedItem, ...] = Field(default=(), max_length=200)
    pending_interrupt: PendingInterrupt | None = None
    error_code: Identifier | None = None

    @model_validator(mode="after")
    def validate_state(self) -> AgentGraphState:
        if self.context.modules != self.plan.modules:
            raise ValueError("frozen context modules must match the execution plan")
        if self.plan.profile != self.budget.profile:
            raise ValueError("execution plan and budget profiles must match")
        if self.checkpoint.context_hash != self.context.analysis_signature:
            raise ValueError("checkpoint context hash does not match the frozen context")
        if self.checkpoint.plan_hash != self.plan.plan_hash:
            raise ValueError("checkpoint plan hash does not match the execution plan")
        if self.checkpoint.last_transition_sequence != len(self.transitions):
            raise ValueError("checkpoint transition cursor does not match graph transitions")
        if self.checkpoint.pending_interrupt != self.pending_interrupt:
            raise ValueError("checkpoint and graph pending interrupts must match")
        outcome_modules = tuple(outcome.module for outcome in self.module_outcomes)
        _require_unique(outcome_modules, "module outcomes")
        _require_canonical_modules(outcome_modules, "module outcomes")

        if self.status in {"ready", "running"}:
            if self.terminal_state is not None or self.pending_interrupt is not None:
                raise ValueError("active graph states cannot contain a terminal outcome")
        elif self.status == "success":
            if self.terminal_state != "success" or self.pending_interrupt is not None:
                raise ValueError("successful graph states require the success terminal state")
        elif self.status == "interrupted":
            if self.terminal_state not in {"needs_clarification", "approval_required"}:
                raise ValueError("interrupted graph states require a typed interrupt state")
            if self.pending_interrupt is None:
                raise ValueError("interrupted graph states require a pending interrupt")
        elif self.status == "stopped":
            if self.terminal_state in {None, "success", "needs_clarification", "approval_required"}:
                raise ValueError("stopped graph states require a non-interrupt terminal state")
            if self.pending_interrupt is not None:
                raise ValueError("stopped graph states cannot contain a pending interrupt")
        return self

    @classmethod
    def initial(
        cls,
        *,
        run_id: UUID,
        context: ContextEnvelope,
        plan: ExecutionPlan,
        budget: BudgetState | None = None,
    ) -> AgentGraphState:
        selected_budget = budget or BudgetState.for_profile(plan.profile)
        return cls(
            run_id=run_id,
            context=context,
            plan=plan,
            checkpoint=GraphCheckpoint(
                context_hash=context.analysis_signature,
                plan_hash=plan.plan_hash,
                next_module=plan.modules[0],
            ),
            budget=selected_budget,
        )


# Concise public alias used by graph factory signatures.
GraphState = AgentGraphState


def _require_unique(values: tuple[object, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _require_canonical_modules(values: tuple[ModuleName, ...], label: str) -> None:
    positions = tuple(MODULE_ORDER.index(value) for value in values)
    if positions != tuple(sorted(positions)):
        raise ValueError(f"{label} must follow Measurement, Assurance, Procurement, Dispatch")


def _sha256_model(model: BaseModel) -> str:
    encoded = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "BUDGET_PROFILES",
    "CONTEXT_TOOL_ORDER",
    "GOLDEN_BUDGET",
    "MODULE_DEPENDENCIES",
    "MODULE_ORDER",
    "MODULE_TOOL_ORDER",
    "RESUME_BUDGET",
    "SINGLE_BUDGET",
    "TOOL_NAMES",
    "AgentGraphState",
    "ApprovalInterrupt",
    "BudgetExhaustedError",
    "BudgetLimits",
    "BudgetProfileName",
    "BudgetState",
    "ClarificationInterrupt",
    "ContextConstraint",
    "ContextEntity",
    "ContextEnvelope",
    "ExecutionPlan",
    "GraphCheckpoint",
    "GraphName",
    "GraphState",
    "GraphStatus",
    "GraphTransition",
    "ImmutableContextEnvelope",
    "ModuleName",
    "ModuleOutcome",
    "PendingInterrupt",
    "PlanStage",
    "StageRepairCount",
    "TerminalState",
    "ToolCallRecord",
    "ToolInvocation",
    "ToolInvoker",
    "ToolInvokerError",
    "ToolName",
    "ToolOutput",
    "ToolResult",
    "UnsupportedItem",
    "VerifiedFactReference",
    "budget_limits_for",
]
