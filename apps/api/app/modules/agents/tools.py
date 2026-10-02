"""Typed, allowlisted tool boundary for the bounded agent graphs.

The graph runtime supplies an already frozen context separately from model-produced
arguments.  Tool handlers therefore cannot select another tenant, receive a database
session, execute SQL, inspect provider credentials, or make an approval decision.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from app.modules.agents.fresh_contracts import FreshRunInputs
from app.modules.agents.graph_contracts import (
    TOOL_NAMES,
    ToolName,
)
from app.modules.agents.graph_contracts import ContextEnvelope as GraphContextEnvelope
from app.modules.agents.schemas import FrozenContextEnvelope, MetricKey

AgentToolName = ToolName
AGENT_TOOL_NAMES: tuple[AgentToolName, ...] = TOOL_NAMES

ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
LongText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]
UnitText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=50,
        pattern=r"^[A-Za-z0-9%./_-]+$",
    ),
]
EventType = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$",
    ),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StrictToolModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )


class StrictToolInput(StrictToolModel):
    """Base for model-produced arguments; tenant selectors are never fields."""

    @field_validator("*", mode="after")
    @classmethod
    def reject_duplicate_tuple_values(cls, value: object) -> object:
        if isinstance(value, tuple):
            try:
                if len(value) != len(set(value)):
                    raise ValueError("tuple values must be unique")
            except TypeError:
                pass
        return value


class ResolveContextInput(StrictToolInput):
    include_metric_definitions: bool = False


class ResolveEntityInput(StrictToolInput):
    entity_type: Literal[
        "site",
        "reporting_period",
        "measurement",
        "standard",
        "supplier",
        "supplier_product",
        "flexible_load",
    ]
    reference: ShortText


class RetrieveLedgerFactsInput(StrictToolInput):
    metric_keys: tuple[MetricKey, ...] = Field(default_factory=tuple, max_length=20)
    entity_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=100)
    limit: int = Field(default=50, ge=1, le=100)


class RetrieveEvidenceInput(StrictToolInput):
    query_text: LongText
    requirement_id: UUID | None = None
    evidence_types: tuple[ShortText, ...] = Field(default_factory=tuple, max_length=10)
    min_support_score: Decimal = Field(default=Decimal(0), ge=0, le=1)
    limit: int = Field(default=10, ge=1, le=20)


class WriteLedgerEventInput(StrictToolInput):
    event_type: EventType
    subject_type: ShortText
    subject_id: UUID
    source_event_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=100)
    fact_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=100)
    evidence_item_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=100)
    method_definition_id: UUID | None = None
    payload_hash: Sha256


class CreateApprovalPreviewInput(StrictToolInput):
    target_type: Literal["disclosure_draft", "procurement_recommendation", "dispatch_recommendation"]
    target_id: UUID
    payload_hash: Sha256
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("expires_at must include a UTC offset")
        return value


class ValidateActivityInput(StrictToolInput):
    activity_record_ids: tuple[UUID, ...] = Field(default=(), max_length=3000)


class NormalizeUnitInput(StrictToolInput):
    activity_record_ids: tuple[UUID, ...] = Field(default=(), max_length=3000)
    canonical_unit: UnitText


class TimeRangeToolInput(StrictToolInput):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def require_timezones(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timestamps must include a UTC offset")
        return value

    @model_validator(mode="after")
    def require_ordered_range(self) -> TimeRangeToolInput:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class SyncGridHistoryInput(TimeRangeToolInput):
    source_mode: Literal["fixture", "live"] = "live"
    fixture_variant: Literal["quality_cases_v1", "complete_q3_v1"] = "quality_cases_v1"


class SelectEmissionFactorInput(StrictToolInput):
    activity_record_ids: tuple[UUID, ...] = Field(default=(), max_length=3000)
    metric_key: MetricKey


class CalculateEmissionsInput(StrictToolInput):
    activity_record_ids: tuple[UUID, ...] = Field(default=(), max_length=3000)
    emission_factor_id: UUID | None = None
    factor_set_hash: Sha256 | None = None
    method_definition_id: UUID | None = None


class CalculateConfidenceInput(StrictToolInput):
    calculation_run_id: UUID | None = None
    measurement_id: UUID | None = None
    method_definition_id: UUID

    @model_validator(mode="after")
    def require_one_calculation_target(self) -> CalculateConfidenceInput:
        if (self.calculation_run_id is None) == (self.measurement_id is None):
            raise ValueError("provide exactly one calculation_run_id or measurement_id")
        return self


class MapStandardRequirementInput(StrictToolInput):
    standard_id: UUID
    requirement_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=100)


class DecomposeClaimInput(StrictToolInput):
    disclosure_draft_id: UUID
    requirement_id: UUID
    max_claims: int = Field(default=20, ge=1, le=50)


class BindClaimFactsInput(StrictToolInput):
    claim_id: UUID
    ledger_event_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)


class ValidateCitationsInput(StrictToolInput):
    claim_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)


class DetectEvidenceGapsInput(StrictToolInput):
    disclosure_draft_id: UUID
    claim_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=100)


class LoadSupplierCandidatesInput(StrictToolInput):
    scenario_id: UUID | None = None
    material_code: ShortText | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ScoreSupplierInput(StrictToolInput):
    scenario_id: UUID
    supplier_product_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    method_definition_id: UUID


class CalculateProcurementImpactInput(StrictToolInput):
    scenario_id: UUID
    supplier_score_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)


class BuildProcurementRecommendationInput(StrictToolInput):
    scenario_id: UUID
    selected_supplier_score_id: UUID


class SyncGridForecastInput(StrictToolInput):
    forecast_start: datetime
    horizon_hours: Literal[6, 24, 48, 72] = 24
    source_mode: Literal["fixture", "live"] = "live"

    @field_validator("forecast_start")
    @classmethod
    def require_forecast_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("forecast_start must include a UTC offset")
        return value


class OptimizeDispatchWindowInput(StrictToolInput):
    scenario_id: UUID | None = None
    forecast_id: UUID
    # The frozen Dispatch scenario owns its immutable method snapshot.  This
    # identifier is optional because a multi-module context may simultaneously
    # carry a distinct Measurement or Procurement method; the domain service
    # remains authoritative and the adapter validates this value when supplied.
    method_definition_id: UUID | None = None


class CalculateDispatchImpactInput(StrictToolInput):
    scenario_id: UUID
    recommendation_id: UUID


class AgentToolContextEntity(StrictToolModel):
    entity_type: ShortText
    entity_id: UUID


class AgentToolContext(StrictToolModel):
    """Immutable, safe copy of the context supplied to deterministic services."""

    company_id: UUID
    fresh_inputs: FreshRunInputs | None = None
    run_id: UUID | None = None
    current_module: str | None = None
    derived: dict[str, JsonValue] = Field(default_factory=dict)
    actor_id: UUID
    actor_role: ShortText
    site_id: UUID | None
    reporting_period_id: UUID | None
    request_hash: Sha256
    analysis_signature: Sha256
    workflow: ShortText | None = None
    modules: tuple[ShortText, ...] = ()
    metric_keys: tuple[str, ...] = ()
    entities: tuple[AgentToolContextEntity, ...] = ()
    method_definition_ids: tuple[UUID, ...] = ()
    material_scope: tuple[str, ...] = ()
    supplier_product_ids: tuple[UUID, ...] = ()
    carbon_measurement_id: UUID | None = None
    current_product_id: UUID | None = None
    method_definition_id: UUID | None = None
    constraints: dict[str, JsonValue] = Field(default_factory=dict)
    resolved: dict[str, JsonValue] | None = None

    @classmethod
    def from_envelope(
        cls,
        envelope: FrozenContextEnvelope | GraphContextEnvelope,
    ) -> AgentToolContext:
        if isinstance(envelope, GraphContextEnvelope):
            candidate_product_ids = tuple(
                entity.entity_id
                for entity in envelope.entities
                if entity.entity_type == "supplier_product"
            )
            current_product_id = next(
                (
                    entity.entity_id
                    for entity in envelope.entities
                    if entity.entity_type == "current_product"
                ),
                None,
            )
            measurement_id = next(
                (
                    entity.entity_id
                    for entity in envelope.entities
                    if entity.entity_type == "carbon_measurement"
                ),
                None,
            )
            return cls(
                company_id=envelope.company_id,
                fresh_inputs=envelope.fresh_inputs,
                actor_id=envelope.actor_id,
                actor_role=envelope.actor_role,
                site_id=envelope.site_id,
                reporting_period_id=envelope.reporting_period_id,
                modules=envelope.modules,
                metric_keys=envelope.metric_keys,
                material_scope=envelope.material_scope,
                entities=tuple(
                    AgentToolContextEntity(
                        entity_type=entity.entity_type,
                        entity_id=entity.entity_id,
                    )
                    for entity in envelope.entities
                ),
                method_definition_ids=envelope.method_definition_ids,
                supplier_product_ids=candidate_product_ids,
                carbon_measurement_id=measurement_id,
                current_product_id=current_product_id,
                method_definition_id=(
                    envelope.method_definition_ids[0]
                    if envelope.method_definition_ids
                    else None
                ),
                constraints={item.key: item.value for item in envelope.constraints},
                request_hash=envelope.request_hash,
                analysis_signature=envelope.analysis_signature,
            )
        frozen_entities: list[AgentToolContextEntity] = []

        def add_entity(entity_type: str, entity_id: UUID | None) -> None:
            if entity_id is None:
                return
            entity = AgentToolContextEntity(
                entity_type=entity_type,
                entity_id=entity_id,
            )
            if entity not in frozen_entities:
                frozen_entities.append(entity)

        add_entity("carbon_measurement", envelope.carbon_measurement_id)
        add_entity("current_product", envelope.current_product_id)
        for identifier in envelope.activity_record_ids:
            add_entity("activity_record", identifier)
        add_entity("standard", envelope.standard_id)
        add_entity("disclosure_draft", envelope.disclosure_draft_id)
        for identifier in envelope.requirement_ids:
            add_entity("disclosure_requirement", identifier)
        for identifier in envelope.supplier_product_ids:
            add_entity("supplier_product", identifier)
        add_entity("procurement_scenario", envelope.procurement_scenario_id)
        add_entity("flexible_load", envelope.flexible_load_id)
        add_entity("dispatch_scenario", envelope.dispatch_scenario_id)
        add_entity("grid_forecast", envelope.forecast_id)
        add_entity("policy_definition", envelope.policy_definition_id)

        return cls(
            company_id=envelope.company_id,
            fresh_inputs=envelope.fresh_inputs,
            actor_id=envelope.actor_id,
            actor_role=envelope.actor_role,
            site_id=envelope.site_id,
            reporting_period_id=envelope.reporting_period_id,
            workflow=envelope.workflow,
            metric_keys=tuple(envelope.metric_keys),
            entities=tuple(frozen_entities),
            method_definition_ids=(
                (envelope.method_definition_id,)
                if envelope.method_definition_id is not None
                else ()
            ),
            material_scope=tuple(envelope.material_scope),
            supplier_product_ids=tuple(envelope.supplier_product_ids),
            carbon_measurement_id=envelope.carbon_measurement_id,
            current_product_id=envelope.current_product_id,
            method_definition_id=envelope.method_definition_id,
            constraints={
                **envelope.constraints.model_dump(mode="json"),
                "integration.grid_source_mode": envelope.grid_source_mode,
                **{
                    f"dispatch.{key}": value
                    for key, value in envelope.dispatch_constraints.model_dump(
                        mode="json"
                    ).items()
                    if value not in (None, [])
                },
            },
            resolved=(envelope.resolved.model_dump(mode="json") if envelope.resolved else None),
            request_hash=envelope.request_hash,
            analysis_signature=envelope.analysis_signature,
        )


class AgentToolCacheMetadata(StrictToolModel):
    hit: bool | None = None
    cache_name: ShortText | None = None
    key_hash: Sha256 | None = None
    age_seconds: int | None = Field(default=None, ge=0)


class AgentToolFact(StrictToolModel):
    """A verified fact reference returned by deterministic application code."""

    fact_id: UUID
    metric_key: MetricKey
    ledger_event_id: UUID
    display_value: Annotated[str, StringConstraints(min_length=1, max_length=255)]


type AgentToolStatus = Literal[
    "success",
    "unsupported",
    "no_data",
    "needs_clarification",
    "validation_failed",
    "approval_required",
    "no_feasible_option",
    "stale",
    "provider_unavailable",
    "policy_blocked",
    "budget_exhausted",
    "failed",
]

_UNSAFE_RESULT_KEY_PARTS = (
    "api_key",
    "credential",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "sql",
    "session",
    "decision",
    "decider",
    "document_body",
    "raw_response",
    "system_prompt",
)


def _assert_safe_result_keys(value: JsonValue, *, path: str = "data") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.casefold().replace("-", "_")
            if any(part in normalized for part in _UNSAFE_RESULT_KEY_PARTS):
                raise ValueError(f"unsafe tool result field at {path}.{key}")
            _assert_safe_result_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_safe_result_keys(child, path=f"{path}[{index}]")


class AgentToolResult(StrictToolModel):
    status: AgentToolStatus
    data: dict[str, JsonValue] = Field(default_factory=dict)
    facts: tuple[AgentToolFact, ...] = Field(default_factory=tuple, max_length=100)
    rows: int = Field(default=0, ge=0, le=1_000_000)
    chunks: int = Field(default=0, ge=0, le=10_000)
    cache: AgentToolCacheMetadata = Field(default_factory=AgentToolCacheMetadata)
    code: ShortText | None = None
    message: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None

    @field_validator("data")
    @classmethod
    def keep_data_safe(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _assert_safe_result_keys(value)
        return value

    @classmethod
    def unsupported(cls, tool_name: AgentToolName) -> AgentToolResult:
        return cls(
            status="unsupported",
            code="tool_handler_unavailable",
            message=f"The {tool_name} service handler is unavailable.",
        )


type AgentToolInputModel = type[StrictToolInput]
type AgentToolDomain = Literal[
    "shared",
    "measurement",
    "assurance",
    "procurement",
    "dispatch",
]


@dataclass(frozen=True, slots=True)
class AgentToolDefinition:
    tool_id: AgentToolName
    input_model: AgentToolInputModel
    domain: AgentToolDomain
    mutates_state: bool = False

    @property
    def name(self) -> AgentToolName:
        return self.tool_id


def _definition(
    tool_id: AgentToolName,
    input_model: AgentToolInputModel,
    domain: AgentToolDomain,
    *,
    mutates_state: bool = False,
) -> AgentToolDefinition:
    return AgentToolDefinition(tool_id, input_model, domain, mutates_state)


_DEFINITIONS = (
    _definition("resolve_context", ResolveContextInput, "shared"),
    _definition("resolve_entity", ResolveEntityInput, "shared"),
    _definition("retrieve_ledger_facts", RetrieveLedgerFactsInput, "shared"),
    _definition("retrieve_evidence", RetrieveEvidenceInput, "shared"),
    _definition("write_ledger_event", WriteLedgerEventInput, "shared", mutates_state=True),
    _definition(
        "create_approval_preview",
        CreateApprovalPreviewInput,
        "shared",
        mutates_state=True,
    ),
    _definition("validate_activity", ValidateActivityInput, "measurement"),
    _definition("normalize_unit", NormalizeUnitInput, "measurement"),
    _definition("sync_grid_history", SyncGridHistoryInput, "measurement", mutates_state=True),
    _definition("select_emission_factor", SelectEmissionFactorInput, "measurement"),
    _definition("calculate_emissions", CalculateEmissionsInput, "measurement", mutates_state=True),
    _definition("calculate_confidence", CalculateConfidenceInput, "measurement"),
    _definition("map_standard_requirement", MapStandardRequirementInput, "assurance"),
    _definition("decompose_claim", DecomposeClaimInput, "assurance", mutates_state=True),
    _definition("bind_claim_facts", BindClaimFactsInput, "assurance", mutates_state=True),
    _definition("validate_citations", ValidateCitationsInput, "assurance", mutates_state=True),
    _definition("detect_evidence_gaps", DetectEvidenceGapsInput, "assurance", mutates_state=True),
    _definition("load_supplier_candidates", LoadSupplierCandidatesInput, "procurement"),
    _definition("score_supplier", ScoreSupplierInput, "procurement", mutates_state=True),
    _definition(
        "calculate_procurement_impact",
        CalculateProcurementImpactInput,
        "procurement",
        mutates_state=True,
    ),
    _definition(
        "build_procurement_recommendation",
        BuildProcurementRecommendationInput,
        "procurement",
        mutates_state=True,
    ),
    _definition("sync_grid_forecast", SyncGridForecastInput, "dispatch", mutates_state=True),
    _definition("optimize_dispatch_window", OptimizeDispatchWindowInput, "dispatch", mutates_state=True),
    _definition(
        "calculate_dispatch_impact",
        CalculateDispatchImpactInput,
        "dispatch",
        mutates_state=True,
    ),
)

AGENT_TOOL_DEFINITIONS: Mapping[AgentToolName, AgentToolDefinition] = MappingProxyType(
    {definition.tool_id: definition for definition in _DEFINITIONS}
)

if tuple(AGENT_TOOL_DEFINITIONS) != AGENT_TOOL_NAMES:  # pragma: no cover - import invariant
    raise RuntimeError("agent tool definitions do not match the frozen allowlist")


class AgentToolHandler(Protocol):
    async def __call__(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult | Mapping[str, object] | None: ...


class AgentToolServicePort(Protocol):
    """Provider-neutral adapter from tool IDs to deterministic service handlers."""

    def handler_for(self, tool_name: AgentToolName) -> AgentToolHandler | None: ...


type AgentToolDispatchGuard = Callable[
    [AgentToolDefinition, AgentToolContext, StrictToolInput],
    Awaitable[None] | None,
]


class AgentToolError(ValueError):
    """Safe base error for registry-boundary rejection."""


class UnknownAgentToolError(AgentToolError):
    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Agent tool is not allowlisted: {tool_name!r}.")
        self.tool_name = tool_name


class AgentToolContextOverrideError(AgentToolError):
    def __init__(self) -> None:
        super().__init__("Tool arguments cannot override the frozen agent context.")


class AgentToolPayloadError(AgentToolError):
    def __init__(self, tool_name: AgentToolName) -> None:
        super().__init__(f"Arguments for {tool_name!r} failed typed validation.")
        self.tool_name = tool_name


class AgentToolResultError(AgentToolError):
    def __init__(self, tool_name: AgentToolName) -> None:
        super().__init__(f"The {tool_name!r} service returned an unsafe result.")
        self.tool_name = tool_name


_FROZEN_CONTEXT_FIELDS = frozenset(
    {
        "company_id",
        "actor_id",
        "actor_role",
        "site_id",
        "reporting_period_id",
        "workflow",
        "constraints",
        "request_hash",
        "analysis_signature",
    }
)


class AgentToolRegistry:
    def __init__(self, service_port: AgentToolServicePort | None = None) -> None:
        self._service_port = service_port

    @property
    def definitions(self) -> Mapping[AgentToolName, AgentToolDefinition]:
        return AGENT_TOOL_DEFINITIONS

    def definition(self, tool_name: str) -> AgentToolDefinition:
        try:
            return AGENT_TOOL_DEFINITIONS[tool_name]  # type: ignore[index]
        except KeyError:
            raise UnknownAgentToolError(tool_name) from None

    async def invoke(
        self,
        tool_name: str,
        *,
        context: FrozenContextEnvelope | GraphContextEnvelope,
        payload: Mapping[str, object] | BaseModel | None = None,
        before_dispatch: AgentToolDispatchGuard | None = None,
        execution_run_id: UUID | None = None,
        execution_module: str | None = None,
        derived_outputs: Mapping[str, JsonValue] | None = None,
    ) -> AgentToolResult:
        definition = self.definition(tool_name)
        validated_context = (
            context
            if isinstance(context, GraphContextEnvelope)
            else FrozenContextEnvelope.model_validate(context)
        )
        safe_context = AgentToolContext.from_envelope(validated_context)
        if safe_context.fresh_inputs is not None:
            safe_context = safe_context.model_copy(update={
                "run_id": execution_run_id,
                "current_module": execution_module,
                "derived": dict(derived_outputs or {}),
            })
        raw_payload = self._payload_mapping(definition.tool_id, payload)
        if _FROZEN_CONTEXT_FIELDS.intersection(raw_payload):
            raise AgentToolContextOverrideError
        try:
            arguments = definition.input_model.model_validate(raw_payload)
        except ValidationError:
            raise AgentToolPayloadError(definition.tool_id) from None
        self._enforce_frozen_scope(safe_context, arguments)

        if before_dispatch is not None:
            guard_result = before_dispatch(definition, safe_context, arguments)
            if inspect.isawaitable(guard_result):
                await guard_result

        if self._service_port is None:
            return AgentToolResult.unsupported(definition.tool_id)
        handler_factory = getattr(self._service_port, "handler_for", None)
        if handler_factory is None:
            return AgentToolResult.unsupported(definition.tool_id)
        handler = handler_factory(definition.tool_id)
        if handler is None:
            return AgentToolResult.unsupported(definition.tool_id)

        try:
            result = await handler(context=safe_context, arguments=arguments)
        except NotImplementedError:
            return AgentToolResult.unsupported(definition.tool_id)
        if result is None:
            return AgentToolResult.unsupported(definition.tool_id)
        try:
            return AgentToolResult.model_validate(result)
        except ValidationError:
            raise AgentToolResultError(definition.tool_id) from None

    @staticmethod
    def _payload_mapping(
        tool_name: AgentToolName,
        payload: Mapping[str, object] | BaseModel | None,
    ) -> dict[str, object]:
        if payload is None:
            return {}
        if isinstance(payload, BaseModel):
            return payload.model_dump(mode="python")
        if not isinstance(payload, Mapping):
            raise AgentToolPayloadError(tool_name)
        return dict(payload)

    @staticmethod
    def _enforce_frozen_scope(
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> None:
        if (
            isinstance(arguments, (SyncGridHistoryInput, SyncGridForecastInput))
            and arguments.source_mode != context.constraints.get("integration.grid_source_mode", "live")
        ):
            raise AgentToolContextOverrideError
        requested_metrics = getattr(arguments, "metric_keys", ())
        if (
            requested_metrics
            and context.metric_keys
            and not set(requested_metrics).issubset(context.metric_keys)
        ):
            raise AgentToolContextOverrideError

        requested_products = getattr(arguments, "supplier_product_ids", ())
        if (
            requested_products
            and context.supplier_product_ids
            and not set(requested_products).issubset(context.supplier_product_ids)
        ):
            raise AgentToolContextOverrideError


__all__ = [
    "AGENT_TOOL_DEFINITIONS",
    "AGENT_TOOL_NAMES",
    "AgentToolCacheMetadata",
    "AgentToolContext",
    "AgentToolContextEntity",
    "AgentToolContextOverrideError",
    "AgentToolDefinition",
    "AgentToolDispatchGuard",
    "AgentToolError",
    "AgentToolFact",
    "AgentToolHandler",
    "AgentToolPayloadError",
    "AgentToolRegistry",
    "AgentToolResult",
    "AgentToolResultError",
    "AgentToolServicePort",
    "AgentToolStatus",
    "UnknownAgentToolError",
]
