"""Adapter from LangGraph tool invocations to the strict 24-tool registry."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from decimal import Decimal
from time import perf_counter
from uuid import UUID

from pydantic import BaseModel

from app.modules.agents.graph_contracts import (
    ApprovalInterrupt,
    ClarificationInterrupt,
    ToolInvocation,
    ToolOutput,
    ToolResult,
    UnsupportedItem,
    VerifiedFactReference,
)
from app.modules.agents.step_recorder import (
    AgentStepRecorder,
    DurableToolResultTooLargeError,
    durable_tool_result_snapshot,
)
from app.modules.agents.tools import AgentToolRegistry

ToolPayload = Mapping[str, object] | BaseModel | None
ToolPayloadFactory = Callable[[ToolInvocation], ToolPayload | Awaitable[ToolPayload]]
_UNAVAILABLE = object()


class RegistryGraphToolInvoker:
    """Invoke allowlisted tools and persist attributed start/completion steps."""

    def __init__(
        self,
        registry: AgentToolRegistry,
        *,
        payload_factory: ToolPayloadFactory | None = None,
        recorder: AgentStepRecorder | None = None,
        persistence_lock: asyncio.Lock | None = None,
    ) -> None:
        self._registry = registry
        self._payload_factory = payload_factory or default_tool_payload
        self._recorder = recorder
        self._persistence_lock = persistence_lock

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        if self._persistence_lock is None:
            return await self._invoke_serialized(invocation)
        async with self._persistence_lock:
            return await self._invoke_serialized(invocation)

    async def _invoke_serialized(self, invocation: ToolInvocation) -> ToolResult:
        try:
            return await self._invoke_and_record(invocation)
        except asyncio.CancelledError:
            # Recover before releasing the shared persistence lock. A queued
            # checkpoint must neither use this session concurrently with rollback
            # nor commit domain changes left pending by the cancelled tool.
            if self._recorder is not None:
                await self._recorder.recover_transaction()
            raise

    async def _invoke_and_record(self, invocation: ToolInvocation) -> ToolResult:
        started = perf_counter()
        step_type = _step_type(invocation.tool_name)
        node_name = f"{invocation.graph_name}.{invocation.tool_name}"
        invocation_key = _tool_invocation_key(invocation)
        if self._recorder is not None:
            reservation = await self._recorder.reserve_tool_invocation(
                invocation_key=invocation_key,
                step_type=step_type,
                graph_name=invocation.graph_name,
                node_name=node_name,
                tool_name=invocation.tool_name,
                input_summary={
                    "invocation_key": invocation_key,
                    "analysis_signature": invocation.context.analysis_signature,
                    "checkpoint_revision": invocation.checkpoint_revision,
                },
                data={
                    "tool_name": invocation.tool_name,
                    "graph_name": invocation.graph_name,
                    "stage_id": invocation.stage_id,
                },
            )
            if reservation.result is not None:
                return reservation.result
            if not reservation.reserved:
                return ToolResult(
                    status="stale",
                    code="tool_outcome_indeterminate",
                )
        payload = self._payload_factory(invocation)
        if hasattr(payload, "__await__"):
            payload = await payload  # type: ignore[misc]
        if isinstance(payload, BaseModel):
            payload = payload.model_dump(mode="python")
        definition = self._registry.definition(invocation.tool_name)
        payload_fields = set(payload) if isinstance(payload, Mapping) else set()
        missing_required = {
            name
            for name, field in definition.input_model.model_fields.items()
            if field.is_required() and name not in payload_fields
        }
        if missing_required:
            converted = ToolResult(
                status="unsupported",
                unsupported_items=(
                    UnsupportedItem(
                        code="tool_inputs_unavailable",
                        reason=(
                            "The deterministic service inputs required by this tool are "
                            "not available in the frozen context."
                        ),
                        module=invocation.module,
                        subject_ref=invocation.tool_name,
                    ),
                ),
                code="tool_inputs_unavailable",
            )
            if self._recorder is not None:
                await self._recorder.record(
                    step_type=step_type,
                    event_name="tool.failed",
                    status="blocked",
                    graph_name=invocation.graph_name,
                    node_name=node_name,
                    tool_name=invocation.tool_name,
                    input_summary={"invocation_key": invocation_key},
                    data={
                        "tool_name": invocation.tool_name,
                        "status": "unsupported",
                        "code": "tool_inputs_unavailable",
                        "durable_result": converted.model_dump(mode="json"),
                    },
                )
            return converted
        try:
            fresh_execution = {}
            if invocation.context.fresh_inputs is not None:
                fresh_execution = {
                    "execution_run_id": invocation.run_id,
                    "execution_module": invocation.module,
                    "derived_outputs": {
                        item.key: item.model_dump(mode="json")["value"]
                        for item in invocation.prior_outputs
                    },
                }
            result = await self._registry.invoke(
                invocation.tool_name,
                context=invocation.context,
                payload=payload,
                **fresh_execution,
            )
        except Exception as error:  # noqa: BLE001 - typed registry boundary fails closed
            latency_ms = max(0, round((perf_counter() - started) * 1000))
            converted = ToolResult(
                status="validation_failed",
                code="tool_validation_failed",
            )
            if self._recorder is not None:
                # The handler can have staged domain rows before failing.  Clear
                # that transaction before the failure journal commits, otherwise
                # recording the safe typed result would also commit partial work.
                await self._recorder.recover_transaction()
                await self._recorder.record(
                    step_type=step_type,
                    event_name="tool.failed",
                    status="failed",
                    graph_name=invocation.graph_name,
                    node_name=node_name,
                    tool_name=invocation.tool_name,
                    input_summary={"invocation_key": invocation_key},
                    latency_ms=latency_ms,
                    error_code=getattr(error, "code", "tool_validation_failed"),
                    data={
                        "tool_name": invocation.tool_name,
                        "code": getattr(error, "code", "tool_validation_failed"),
                        "durable_result": converted.model_dump(mode="json"),
                    },
                )
            return converted

        converted = _bounded_durable_result(_convert_result(invocation, result))
        latency_ms = max(0, round((perf_counter() - started) * 1000))
        if self._recorder is not None:
            event_name = (
                "tool.completed" if converted.status == "success" else "tool.failed"
            )
            await self._recorder.record(
                step_type=step_type,
                event_name=event_name,
                status="completed" if converted.status == "success" else "blocked",
                graph_name=invocation.graph_name,
                node_name=node_name,
                tool_name=invocation.tool_name,
                input_summary={"invocation_key": invocation_key},
                latency_ms=latency_ms,
                error_code=None if converted.status == "success" else converted.code,
                data={
                    "tool_name": invocation.tool_name,
                    "status": converted.status,
                    "rows": result.rows,
                    "chunks": result.chunks,
                    "cache_hit": result.cache.hit,
                    "code": converted.code,
                    "durable_result": converted.model_dump(mode="json"),
                },
            )
            if converted.code != "tool_result_too_large":
                for fact in result.facts:
                    await self._recorder.record(
                        step_type="validation",
                        event_name="fact.created",
                        status="completed",
                        graph_name=invocation.graph_name,
                        node_name=node_name,
                        tool_name=invocation.tool_name,
                        data={
                            "fact_id": str(fact.fact_id),
                            "metric_key": fact.metric_key,
                            "ledger_event_id": str(fact.ledger_event_id),
                        },
                    )
        return converted


def _bounded_durable_result(result: ToolResult) -> ToolResult:
    """Fail closed with a replayable result when a handler output is oversized."""

    try:
        durable_tool_result_snapshot(result)
    except DurableToolResultTooLargeError:
        return ToolResult(
            status="validation_failed",
            code="tool_result_too_large",
        )
    return result


def default_tool_payload(invocation: ToolInvocation) -> ToolPayload:
    """Derive typed service arguments only from frozen context/service outputs."""

    prior = {item.key: item.value for item in invocation.prior_outputs}
    context = invocation.context
    entity_ids = tuple(entity.entity_id for entity in context.entities)
    frozen_requirement_ids = tuple(
        entity.entity_id
        for entity in context.entities
        if entity.entity_type == "disclosure_requirement"
    )
    requirement_ids = prior.get("requirement_ids") or frozen_requirement_ids
    constraints = {item.key: item.value for item in context.constraints}
    method_id = context.method_definition_ids[0] if context.method_definition_ids else None
    activity_ids = prior.get("activity_record_ids") or tuple(
        entity.entity_id
        for entity in context.entities
        if entity.entity_type == "activity_record"
    )
    if invocation.tool_name == "resolve_context":
        return {"include_metric_definitions": True}
    if invocation.tool_name == "resolve_entity":
        return {
            "entity_type": prior.get("entity_type", "site"),
            "reference": prior.get("entity_reference", "current"),
        }
    if invocation.tool_name == "retrieve_ledger_facts":
        return {
            "metric_keys": context.metric_keys,
            "entity_ids": entity_ids,
            "limit": 50,
        }
    if invocation.tool_name == "retrieve_evidence":
        return _available_payload(
            query_text=prior.get("evidence_query") or prior.get("claim_text"),
            requirement_id=prior.get("requirement_id") or _first(requirement_ids),
            evidence_types=prior.get("evidence_types", ()),
            limit=10,
        )
    if invocation.tool_name in {"validate_activity", "normalize_unit"}:
        return _available_payload(
            activity_record_ids=activity_ids,
            canonical_unit=(
                prior.get("canonical_unit")
                if invocation.tool_name == "normalize_unit"
                else _UNAVAILABLE
            ),
        )
    if invocation.tool_name == "sync_grid_history":
        return _available_payload(
            start=context.fresh_inputs.history_start if context.fresh_inputs else prior.get("period_start"),
            end=context.fresh_inputs.history_end if context.fresh_inputs else prior.get("period_end"),
            source_mode=constraints.get("integration.grid_source_mode", "live"),
        )
    if invocation.tool_name == "select_emission_factor":
        return _available_payload(
            activity_record_ids=activity_ids,
            metric_key=prior.get("metric_key") or _first(context.metric_keys),
        )
    if invocation.tool_name == "calculate_emissions":
        if context.fresh_inputs is not None:
            return {}
        return _available_payload(
            activity_record_ids=activity_ids,
            emission_factor_id=prior.get("emission_factor_id"),
            factor_set_hash=prior.get("factor_set_hash"),
            method_definition_id=prior.get("method_definition_id") or method_id,
        )
    if invocation.tool_name == "calculate_confidence":
        measurement_id = prior.get("measurement_id")
        return _available_payload(
            calculation_run_id=(
                _UNAVAILABLE if measurement_id is not None else prior.get("calculation_run_id")
            ),
            measurement_id=measurement_id,
            method_definition_id=prior.get("method_definition_id") or method_id,
        )
    if invocation.tool_name == "map_standard_requirement":
        return _available_payload(
            standard_id=prior.get("standard_id") or _entity(context, "standard"),
            requirement_ids=prior.get("requirement_ids") or requirement_ids,
        )
    if invocation.tool_name == "decompose_claim":
        return _available_payload(
            disclosure_draft_id=prior.get("disclosure_draft_id")
            or _entity(context, "disclosure_draft"),
            requirement_id=prior.get("requirement_id") or _first(requirement_ids),
            max_claims=20,
        )
    if invocation.tool_name == "bind_claim_facts":
        return _available_payload(
            claim_id=prior.get("claim_id"),
            ledger_event_ids=prior.get("ledger_event_ids"),
        )
    if invocation.tool_name == "validate_citations":
        return _available_payload(claim_ids=prior.get("claim_ids"))
    if invocation.tool_name == "detect_evidence_gaps":
        return _available_payload(
            disclosure_draft_id=prior.get("disclosure_draft_id")
            or _entity(context, "disclosure_draft"),
            claim_ids=prior.get("claim_ids", ()),
        )
    if invocation.tool_name == "load_supplier_candidates":
        return _available_payload(
            scenario_id=prior.get("scenario_id")
            or _entity(context, "procurement_scenario"),
            material_code=prior.get("material_code") or _first(context.material_scope),
            limit=20,
        )
    if invocation.tool_name == "score_supplier":
        return _available_payload(
            scenario_id=prior.get("scenario_id")
            or _entity(context, "procurement_scenario"),
            supplier_product_ids=(
                prior.get("supplier_product_ids")
                or tuple(
                    entity.entity_id
                    for entity in context.entities
                    if entity.entity_type == "supplier_product"
                )
            ),
            method_definition_id=prior.get("method_definition_id") or method_id,
        )
    if invocation.tool_name == "calculate_procurement_impact":
        return _available_payload(
            scenario_id=prior.get("scenario_id")
            or _entity(context, "procurement_scenario"),
            supplier_score_ids=prior.get("supplier_score_ids"),
        )
    if invocation.tool_name == "build_procurement_recommendation":
        return _available_payload(
            scenario_id=prior.get("scenario_id")
            or _entity(context, "procurement_scenario"),
            selected_supplier_score_id=prior.get("selected_supplier_score_id"),
        )
    if invocation.tool_name == "sync_grid_forecast":
        return _available_payload(
            forecast_start=prior.get("forecast_start")
            or constraints.get("dispatch.window_start"),
            horizon_hours=prior.get("horizon_hours", 24),
            source_mode=constraints.get("integration.grid_source_mode", "live"),
        )
    if invocation.tool_name == "optimize_dispatch_window":
        return _available_payload(
            scenario_id=prior.get("dispatch_scenario_id")
            or _entity(context, "dispatch_scenario")
            or (prior.get("scenario_id") if context.fresh_inputs is None else None),
            forecast_id=prior.get("forecast_id"),
            method_definition_id=prior.get("dispatch_method_definition_id")
            or _entity(context, "dispatch_method_definition"),
        )
    if invocation.tool_name == "calculate_dispatch_impact":
        return _available_payload(
            scenario_id=prior.get("dispatch_scenario_id")
            or _entity(context, "dispatch_scenario")
            or prior.get("scenario_id"),
            recommendation_id=prior.get("dispatch_recommendation_id")
            or prior.get("recommendation_id"),
        )
    if invocation.tool_name == "write_ledger_event":
        return _available_payload(
            event_type=prior.get("ledger_event_type"),
            subject_type=prior.get("ledger_subject_type"),
            subject_id=prior.get("ledger_subject_id"),
            source_event_ids=prior.get("source_event_ids", ()),
            fact_ids=prior.get("fact_ids", ()),
            evidence_item_ids=prior.get("evidence_item_ids", ()),
            method_definition_id=prior.get("method_definition_id") or method_id,
            payload_hash=prior.get("payload_hash"),
        )
    if invocation.tool_name == "create_approval_preview":
        return _available_payload(
            target_type=prior.get("approval_target_type") or prior.get("target_type"),
            target_id=prior.get("approval_target_id") or prior.get("target_id"),
            payload_hash=prior.get("payload_hash"),
            expires_at=prior.get("approval_expires_at"),
        )
    return {}


def _convert_result(invocation: ToolInvocation, result) -> ToolResult:
    status = result.status
    if status == "failed":
        status = "validation_failed"
    outputs_list: list[ToolOutput] = []
    for key, value in result.data.items():
        converted_value = _tool_value(value)
        if converted_value is not _UNAVAILABLE and _is_identifier(key):
            outputs_list.append(ToolOutput(key=key, value=converted_value))
    outputs = tuple(outputs_list)
    unsupported = ()
    if status == "unsupported":
        unsupported = (
            UnsupportedItem(
                code=result.code or "tool_unsupported",
                reason=result.message or "The deterministic service handler is unavailable.",
                module=invocation.module,
                subject_ref=invocation.tool_name,
            ),
        )
    pending = None
    if status == "approval_required":
        pending = _approval_interrupt(invocation, result.data)
    elif status == "needs_clarification":
        pending = _clarification_interrupt(invocation, result.data, result.message)
    if status in {"approval_required", "needs_clarification"} and pending is None:
        return ToolResult(
            status="validation_failed",
            code="interrupt_payload_invalid",
        )
    return ToolResult(
        status=status,
        outputs=outputs,
        facts=tuple(
            VerifiedFactReference(
                fact_id=fact.fact_id,
                metric_key=fact.metric_key,
                ledger_event_id=fact.ledger_event_id,
                display_value=fact.display_value,
            )
            for fact in result.facts
        ),
        unsupported_items=unsupported,
        pending_interrupt=pending,
        rows_processed=result.rows,
        evidence_chunks_retrieved=result.chunks,
        cache_hit=result.cache.hit is True,
        code=result.code,
    )


def _approval_interrupt(
    invocation: ToolInvocation,
    data: Mapping[str, object],
) -> ApprovalInterrupt | None:
    try:
        approval_context_hash = data.get("approval_context_hash") or data.get(
            "context_hash"
        )
        return ApprovalInterrupt(
            approval_id=UUID(str(data["approval_id"])),
            target_type=str(data["target_type"]),
            target_id=UUID(str(data["target_id"])),
            preview_hash=str(data["preview_hash"]),
            analysis_signature=str(data["analysis_signature"]),
            context_hash=invocation.context.analysis_signature,
            expires_at=datetime.fromisoformat(str(data["expires_at"])),
            approval_context_hash=(
                str(approval_context_hash)
                if approval_context_hash is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _tool_invocation_key(invocation: ToolInvocation) -> str:
    encoded = json.dumps(
        {
            "run_id": str(invocation.run_id),
            "graph_name": invocation.graph_name,
            "module": invocation.module,
            "stage_id": invocation.stage_id,
            "tool_name": invocation.tool_name,
            "context_hash": invocation.context.analysis_signature,
            "prior_outputs": [
                output.model_dump(mode="json") for output in invocation.prior_outputs
            ],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _clarification_interrupt(
    invocation: ToolInvocation,
    data: Mapping[str, object],
    message: str | None,
) -> ClarificationInterrupt | None:
    required_fields = data.get("required_fields")
    if not isinstance(required_fields, list) or not all(
        isinstance(item, str) for item in required_fields
    ):
        return None
    try:
        return ClarificationInterrupt(
            code=str(data.get("code") or "tool_clarification_required"),
            message=message or "Additional context is required to continue safely.",
            required_fields=tuple(required_fields),
            context_hash=invocation.context.analysis_signature,
        )
    except (TypeError, ValueError):
        return None


def _step_type(tool_name: str) -> str:
    if tool_name in {"retrieve_ledger_facts", "retrieve_evidence"}:
        return "retrieval"
    return "tool"


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, bool, Decimal, UUID, datetime))


def _tool_value(value: object) -> object:
    if _is_scalar(value):
        return value
    if isinstance(value, (list, tuple)) and len(value) <= 100 and all(
        _is_scalar(item) for item in value
    ):
        return tuple(value)
    return _UNAVAILABLE


def _available_payload(**values: object) -> dict[str, object]:
    return {
        key: value
        for key, value in values.items()
        if value is not _UNAVAILABLE and value is not None
    }


def _first(values: tuple[object, ...]) -> object:
    return values[0] if values else _UNAVAILABLE


def _entity(invocation_context, entity_type: str) -> object:
    return next(
        (
            entity.entity_id
            for entity in invocation_context.entities
            if entity.entity_type == entity_type
        ),
        _UNAVAILABLE,
    )


def _is_identifier(value: str) -> bool:
    return bool(value) and value[0].islower() and all(
        character.islower() or character.isdigit() or character in "_.:-"
        for character in value
    )
