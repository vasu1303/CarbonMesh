from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import and_, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ai import AgentRunStep
from app.db.models.assurance import DisclosureDraft, DisclosureRequirement, Standard
from app.db.models.carbon import (
    ActivityRecord,
    AgentRun,
    CalculationRun,
    CarbonMeasurement,
    EmissionCalculation,
)
from app.db.models.core import Actor, EvidenceItem, ReportingPeriod, Site
from app.db.models.dispatch import DispatchScenario, FlexibleLoad, GridForecast
from app.db.models.ledger import FactBinding, LedgerEvent, LedgerEventEvidence, LineageEdge
from app.db.models.procurement import ProcurementScenario, SupplierProduct
from app.db.models.semantic import MethodDefinition, MetricDefinition, PolicyDefinition
from app.modules.agents.graph_contracts import ToolResult
from app.modules.agents.schemas import AgentExecutionLease, AgentTelemetry

MAX_AGENT_RUN_STEP_PAGE_SIZE = 500


@dataclass(frozen=True, slots=True)
class ResolvedContextReferences:
    actor_role: str


@dataclass(frozen=True, slots=True)
class MeasurementActivityProductInput:
    measurement: CarbonMeasurement
    activity: ActivityRecord
    product: SupplierProduct


@dataclass(frozen=True, slots=True)
class NaturalLanguageContextResolution:
    site_id: UUID | None
    reporting_period_id: UUID | None


@dataclass(frozen=True, slots=True)
class DurableToolInvocation:
    """Existing or newly reserved durable execution state for one graph tool node."""

    reserved: bool
    result: ToolResult | None = None


@dataclass(frozen=True, slots=True)
class RecoverableAgentRun:
    company_id: UUID
    run_id: UUID
    claim: AgentExecutionLease
    mode: Literal["checkpoint", "planned", "fail_closed"]


class ContextReferenceNotFoundError(LookupError):
    def __init__(self, entity_type: str) -> None:
        super().__init__(f"{entity_type} was not found in the requested company")
        self.entity_type = entity_type


class NaturalLanguageContextAmbiguousError(LookupError):
    def __init__(self, entity_type: str) -> None:
        super().__init__(f"more than one {entity_type} matches the requested reference")
        self.entity_type = entity_type


class AgentRunStepParentNotFoundError(LookupError):
    """Raised when a step cannot be attached to the requested tenant-scoped run."""

    def __init__(self) -> None:
        super().__init__("agent run was not found in the requested company")


class AgentWorkflowRepository:
    """Bounded persistence reads used by the deterministic agent workflow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_measurement_inputs(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        reporting_period_id: UUID,
        metric_key: str,
        carbon_measurement_id: UUID | None,
        current_product_id: UUID | None,
        limit: int,
    ) -> list[MeasurementActivityProductInput]:
        statement = (
            select(CarbonMeasurement, ActivityRecord, SupplierProduct)
            .join(
                MetricDefinition,
                and_(
                    MetricDefinition.company_id == CarbonMeasurement.company_id,
                    MetricDefinition.id == CarbonMeasurement.metric_definition_id,
                ),
            )
            .join(
                EmissionCalculation,
                and_(
                    EmissionCalculation.company_id == CarbonMeasurement.company_id,
                    EmissionCalculation.calculation_run_id == CarbonMeasurement.calculation_run_id,
                ),
            )
            .join(
                ActivityRecord,
                and_(
                    ActivityRecord.company_id == EmissionCalculation.company_id,
                    ActivityRecord.id == EmissionCalculation.activity_record_id,
                ),
            )
            .join(
                SupplierProduct,
                and_(
                    SupplierProduct.company_id == ActivityRecord.company_id,
                    SupplierProduct.id == ActivityRecord.supplier_product_id,
                ),
            )
            .where(
                CarbonMeasurement.company_id == company_id,
                CarbonMeasurement.site_id == site_id,
                CarbonMeasurement.reporting_period_id == reporting_period_id,
                CarbonMeasurement.status == "verified",
                MetricDefinition.key == metric_key,
                ActivityRecord.status == "valid",
            )
            .order_by(CarbonMeasurement.verified_at.desc(), ActivityRecord.id)
            .limit(limit)
        )
        if carbon_measurement_id is not None:
            statement = statement.where(CarbonMeasurement.id == carbon_measurement_id)
        if current_product_id is not None:
            statement = statement.where(ActivityRecord.supplier_product_id == current_product_id)

        rows = (await self._session.execute(statement)).all()
        return [
            MeasurementActivityProductInput(
                measurement=row[0],
                activity=row[1],
                product=row[2],
            )
            for row in rows
        ]

    async def get_reporting_period(
        self,
        *,
        company_id: UUID,
        reporting_period_id: UUID,
    ) -> ReportingPeriod | None:
        return await self._session.scalar(
            select(ReportingPeriod).where(
                ReportingPeriod.company_id == company_id,
                ReportingPeriod.id == reporting_period_id,
            )
        )

    async def list_scoring_methods(
        self,
        *,
        company_id: UUID,
        period_start: date,
        period_end: date,
        method_definition_id: UUID | None,
        limit: int,
    ) -> list[MethodDefinition]:
        statement = (
            select(MethodDefinition)
            .where(
                MethodDefinition.company_id == company_id,
                MethodDefinition.method_type == "supplier_scoring",
                MethodDefinition.is_active.is_(True),
                MethodDefinition.effective_from <= period_end,
                or_(
                    MethodDefinition.effective_to.is_(None),
                    MethodDefinition.effective_to >= period_start,
                ),
            )
            .order_by(MethodDefinition.version.desc(), MethodDefinition.id)
            .limit(limit)
        )
        if method_definition_id is not None:
            statement = statement.where(MethodDefinition.id == method_definition_id)
        return list((await self._session.scalars(statement)).all())


class AgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_context_references(
        self,
        *,
        company_id: UUID,
        actor_id: UUID,
        site_id: UUID | None = None,
        reporting_period_id: UUID | None = None,
        carbon_measurement_id: UUID | None = None,
        current_product_id: UUID | None = None,
        method_definition_id: UUID | None = None,
        activity_record_ids: list[UUID] | None = None,
        supplier_product_ids: list[UUID] | None = None,
        standard_id: UUID | None = None,
        disclosure_draft_id: UUID | None = None,
        requirement_ids: list[UUID] | None = None,
        evidence_item_ids: list[UUID] | None = None,
        procurement_scenario_id: UUID | None = None,
        flexible_load_id: UUID | None = None,
        dispatch_scenario_id: UUID | None = None,
        forecast_id: UUID | None = None,
        policy_definition_id: UUID | None = None,
    ) -> ResolvedContextReferences:
        actor_role = await self._session.scalar(
            select(Actor.role).where(
                Actor.company_id == company_id,
                Actor.id == actor_id,
                Actor.is_active.is_(True),
            )
        )
        if actor_role is None:
            raise ContextReferenceNotFoundError("actor")

        if site_id is not None:
            resolved_site_id = await self._session.scalar(
                select(Site.id).where(
                    Site.company_id == company_id,
                    Site.id == site_id,
                    Site.is_active.is_(True),
                )
            )
            if resolved_site_id is None:
                raise ContextReferenceNotFoundError("site")

        if reporting_period_id is not None:
            resolved_period_id = await self._session.scalar(
                select(ReportingPeriod.id).where(
                    ReportingPeriod.company_id == company_id,
                    ReportingPeriod.id == reporting_period_id,
                )
            )
            if resolved_period_id is None:
                raise ContextReferenceNotFoundError("reporting_period")

        references: tuple[tuple[type[Any], UUID | None, str], ...] = (
            (CarbonMeasurement, carbon_measurement_id, "carbon_measurement"),
            (SupplierProduct, current_product_id, "current_product"),
            (MethodDefinition, method_definition_id, "method_definition"),
            (Standard, standard_id, "standard"),
            (DisclosureDraft, disclosure_draft_id, "disclosure_draft"),
            (ProcurementScenario, procurement_scenario_id, "procurement_scenario"),
            (FlexibleLoad, flexible_load_id, "flexible_load"),
            (DispatchScenario, dispatch_scenario_id, "dispatch_scenario"),
            (GridForecast, forecast_id, "forecast"),
            (PolicyDefinition, policy_definition_id, "policy_definition"),
        )
        for model, entity_id, label in references:
            if entity_id is None:
                continue
            resolved_id = await self._session.scalar(
                select(model.id).where(
                    model.company_id == company_id,
                    model.id == entity_id,
                )
            )
            if resolved_id is None:
                raise ContextReferenceNotFoundError(label)

        collections: tuple[tuple[type[Any], list[UUID], str], ...] = (
            (ActivityRecord, activity_record_ids or [], "activity_record"),
            (SupplierProduct, supplier_product_ids or [], "supplier_product"),
            (DisclosureRequirement, requirement_ids or [], "requirement"),
            (EvidenceItem, evidence_item_ids or [], "evidence_item"),
        )
        for model, entity_ids, label in collections:
            if not entity_ids:
                continue
            resolved_count = await self._session.scalar(
                select(func.count()).select_from(model).where(
                    model.company_id == company_id,
                    model.id.in_(entity_ids),
                )
            )
            if int(resolved_count or 0) != len(set(entity_ids)):
                raise ContextReferenceNotFoundError(label)

        return ResolvedContextReferences(actor_role=actor_role)

    async def resolve_natural_language_context(
        self,
        *,
        company_id: UUID,
        site_reference: str | None,
        period_start: date | None,
        period_end: date | None,
    ) -> NaturalLanguageContextResolution:
        """Resolve only exact, tenant-scoped names parsed by the contextualizer."""

        site_id: UUID | None = None
        if site_reference is not None:
            normalized = site_reference.strip().casefold()
            site_ids = list(
                (
                    await self._session.scalars(
                        select(Site.id)
                        .where(
                            Site.company_id == company_id,
                            Site.is_active.is_(True),
                            or_(
                                func.lower(Site.code) == normalized,
                                func.lower(Site.name) == normalized,
                            ),
                        )
                        .order_by(Site.id)
                        .limit(2)
                    )
                ).all()
            )
            if len(site_ids) > 1:
                raise NaturalLanguageContextAmbiguousError("site")
            site_id = site_ids[0] if site_ids else None

        reporting_period_id: UUID | None = None
        if period_start is not None and period_end is not None:
            period_ids = list(
                (
                    await self._session.scalars(
                        select(ReportingPeriod.id)
                        .where(
                            ReportingPeriod.company_id == company_id,
                            ReportingPeriod.start_date == period_start,
                            ReportingPeriod.end_date == period_end,
                        )
                        .order_by(ReportingPeriod.id)
                        .limit(2)
                    )
                ).all()
            )
            if len(period_ids) > 1:
                raise NaturalLanguageContextAmbiguousError("reporting_period")
            reporting_period_id = period_ids[0] if period_ids else None

        return NaturalLanguageContextResolution(
            site_id=site_id,
            reporting_period_id=reporting_period_id,
        )

    async def planning_catalog(self, *, company_id: UUID) -> dict[str, object]:
        """Expose a bounded semantic projection, never source bodies or formulas."""
        metrics = (await self._session.execute(select(
            MetricDefinition.key, MetricDefinition.version, MetricDefinition.canonical_unit,
        ).where(MetricDefinition.company_id == company_id, MetricDefinition.is_active.is_(True))
         .order_by(MetricDefinition.key, MetricDefinition.version).limit(20))).all()
        methods = (await self._session.execute(select(
            MethodDefinition.key, MethodDefinition.version, MethodDefinition.method_type,
        ).where(MethodDefinition.company_id == company_id, MethodDefinition.is_active.is_(True))
         .order_by(MethodDefinition.key, MethodDefinition.version).limit(10))).all()
        return {
            "metrics": [{"key": key, "version": version, "unit": unit} for key, version, unit in metrics],
            "methods": [{"key": key, "version": version, "type": kind} for key, version, kind in methods],
        }

    async def domain_ledger_write_proof(
        self, *, company_id: UUID, run_id: UUID, event_type: str,
        subject_type: str, subject_id: UUID, payload_hash: str,
    ):
        event = await self._session.scalar(select(LedgerEvent).where(
            LedgerEvent.company_id == company_id, LedgerEvent.event_type == event_type,
            LedgerEvent.entity_type == subject_type, LedgerEvent.entity_id == subject_id,
            LedgerEvent.payload_hash == payload_hash,
        ).order_by(LedgerEvent.created_at).limit(1))
        if event is None:
            return None
        method_id = None
        owns_event = event.agent_run_id == run_id
        if subject_type == "carbon_measurement":
            calculation = await self._session.scalar(select(CalculationRun).join(
                CarbonMeasurement, CarbonMeasurement.calculation_run_id == CalculationRun.id,
            ).where(CarbonMeasurement.company_id == company_id, CarbonMeasurement.id == subject_id,
                    CalculationRun.company_id == company_id, CalculationRun.agent_run_id == run_id))
            if calculation is not None:
                owns_event = True
                method_id = calculation.method_definition_id
        if not owns_event:
            return None
        source_ids = set(await self._session.scalars(select(LineageEdge.parent_event_id).where(
            LineageEdge.company_id == company_id, LineageEdge.child_event_id == event.id,
        )))
        evidence_ids = set(await self._session.scalars(select(LedgerEventEvidence.evidence_item_id).where(
            LedgerEventEvidence.company_id == company_id, LedgerEventEvidence.ledger_event_id == event.id,
        )))
        fact_ids = set(await self._session.scalars(select(FactBinding.id).where(
            FactBinding.company_id == company_id, FactBinding.ledger_event_id == event.id,
        ))) | {event.entity_id}
        return event.id, event.payload_hash, source_ids, fact_ids, evidence_ids, method_id

    async def add(self, run: AgentRun) -> None:
        self._session.add(run)
        await self._session.flush()

    async def get(self, *, company_id: UUID, run_id: UUID) -> AgentRun | None:
        return await self._session.scalar(
            select(AgentRun).where(
                AgentRun.company_id == company_id,
                AgentRun.id == run_id,
            )
        )

    async def get_for_update(self, *, company_id: UUID, run_id: UUID) -> AgentRun | None:
        """Lock one tenant-scoped run for a durable resume or terminal transition."""

        return await self._session.scalar(
            select(AgentRun)
            .where(
                AgentRun.company_id == company_id,
                AgentRun.id == run_id,
            )
            .with_for_update()
        )

    async def try_claim_execution(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        lease_seconds: int,
        claim_id: UUID | None = None,
        recovered: bool = False,
        now: datetime | None = None,
    ) -> AgentExecutionLease | None:
        """Atomically fence one running execution using the parent-row lock."""

        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        observed_at = now or datetime.now(UTC)
        run = await self.get_for_update(company_id=company_id, run_id=run_id)
        if run is None or run.terminal_state != "running":
            return None
        telemetry = AgentTelemetry.model_validate(run.telemetry)
        current = telemetry.execution_lease
        if current is not None and current.expires_at > observed_at:
            return None
        lease = AgentExecutionLease(
            claim_id=claim_id or uuid4(),
            claimed_at=observed_at,
            expires_at=observed_at + timedelta(seconds=lease_seconds),
            attempt=telemetry.execution_attempts + 1,
            recovered=recovered,
        )
        run.telemetry = telemetry.model_copy(
            update={
                "execution_attempts": lease.attempt,
                "execution_lease": lease,
            }
        ).model_dump(mode="json")
        return lease

    async def renew_execution_claim(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        claim_id: UUID,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> AgentExecutionLease | None:
        """Refresh a lease only while its fencing token still owns the run."""

        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        observed_at = now or datetime.now(UTC)
        run = await self.get_for_update(company_id=company_id, run_id=run_id)
        if run is None or run.terminal_state != "running":
            return None
        telemetry = AgentTelemetry.model_validate(run.telemetry)
        current = telemetry.execution_lease
        if current is None or current.claim_id != claim_id:
            return None
        renewed = current.model_copy(
            update={"expires_at": observed_at + timedelta(seconds=lease_seconds)}
        )
        run.telemetry = telemetry.model_copy(
            update={"execution_lease": renewed}
        ).model_dump(mode="json")
        return renewed

    async def release_execution_claim(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        claim_id: UUID,
    ) -> bool:
        """Clear a lease only when the caller still owns its fencing token."""

        run = await self.get_for_update(company_id=company_id, run_id=run_id)
        if run is None:
            return False
        telemetry = AgentTelemetry.model_validate(run.telemetry)
        current = telemetry.execution_lease
        if current is None or current.claim_id != claim_id:
            return False
        run.telemetry = telemetry.model_copy(
            update={"execution_lease": None}
        ).model_dump(mode="json")
        return True

    async def claim_next_recoverable(
        self,
        *,
        lease_seconds: int,
        scan_limit: int = 25,
        now: datetime | None = None,
    ) -> RecoverableAgentRun | None:
        """Claim one expired checkpointed run without blocking another worker."""

        if not 1 <= scan_limit <= 100:
            raise ValueError("scan_limit must be between 1 and 100")
        observed_at = now or datetime.now(UTC)
        cursor: tuple[datetime, UUID] | None = None
        while True:
            statement = select(AgentRun).where(
                AgentRun.terminal_state == "running",
                AgentRun.stage.in_(
                    (
                        "queued",
                        "planned",
                        "graph",
                        "resumed_clarification",
                        "resumed_approval",
                    )
                ),
            )
            if cursor is not None:
                started_at, run_id = cursor
                statement = statement.where(
                    or_(
                        AgentRun.started_at > started_at,
                        and_(
                            AgentRun.started_at == started_at,
                            AgentRun.id > run_id,
                        ),
                    )
                )
            candidates = list(
                (
                    await self._session.scalars(
                        statement.order_by(AgentRun.started_at, AgentRun.id)
                        .limit(scan_limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            if not candidates:
                return None
            for run in candidates:
                telemetry = AgentTelemetry.model_validate(run.telemetry)
                current = telemetry.execution_lease
                if current is not None and current.expires_at > observed_at:
                    continue
                if telemetry.graph_state is not None:
                    mode: Literal["checkpoint", "planned", "fail_closed"] = "checkpoint"
                elif (
                    run.stage == "planned"
                    and run.plan is not None
                    and telemetry.graph_context is not None
                ):
                    mode = "planned"
                elif run.started_at > observed_at - timedelta(seconds=lease_seconds):
                    # A newly queued run may not have reached its background worker
                    # yet. Only fail closed after a complete lease window elapsed.
                    continue
                else:
                    mode = "fail_closed"
                lease = AgentExecutionLease(
                    claim_id=uuid4(),
                    claimed_at=observed_at,
                    expires_at=observed_at + timedelta(seconds=lease_seconds),
                    attempt=telemetry.execution_attempts + 1,
                    recovered=True,
                )
                run.telemetry = telemetry.model_copy(
                    update={
                        "execution_attempts": lease.attempt,
                        "execution_lease": lease,
                    }
                ).model_dump(mode="json")
                return RecoverableAgentRun(
                    company_id=run.company_id,
                    run_id=run.id,
                    claim=lease,
                    mode=mode,
                )
            if len(candidates) < scan_limit:
                return None
            last = candidates[-1]
            cursor = (last.started_at, last.id)

    async def reserve_tool_invocation(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        invocation_key: str,
        step_type: str,
        graph_name: str,
        node_name: str,
        tool_name: str,
        input_snapshot: dict[str, Any],
        output_snapshot: dict[str, Any],
        started_at: datetime,
    ) -> DurableToolInvocation:
        """Reserve a graph node before dispatch, or replay its durable result."""

        locked = await self.get_for_update(company_id=company_id, run_id=run_id)
        if locked is None:
            raise AgentRunStepParentNotFoundError
        entries = list(
            (
                await self._session.scalars(
                    select(AgentRunStep)
                    .where(
                        AgentRunStep.company_id == company_id,
                        AgentRunStep.agent_run_id == run_id,
                        AgentRunStep.graph_name == graph_name,
                        AgentRunStep.node_name == node_name,
                        AgentRunStep.tool_name == tool_name,
                        AgentRunStep.input_snapshot[
                            "invocation_key"
                        ].as_string()
                        == invocation_key,
                    )
                    .order_by(AgentRunStep.sequence.desc())
                    .limit(10)
                )
            ).all()
        )
        reserved = False
        for entry in entries:
            entry_output = entry.output_snapshot if isinstance(entry.output_snapshot, dict) else {}
            data = entry_output.get("data")
            if isinstance(data, dict) and isinstance(data.get("durable_result"), dict):
                return DurableToolInvocation(
                    reserved=False,
                    result=ToolResult.model_validate_json(
                        json.dumps(data["durable_result"])
                    ),
                )
            reserved = True
        if reserved:
            return DurableToolInvocation(reserved=False)

        await self._append_locked_step(
            company_id=company_id,
            run_id=run_id,
            step_type=step_type,
            status="running",
            graph_name=graph_name,
            node_name=node_name,
            tool_name=tool_name,
            input_snapshot=input_snapshot,
            output_snapshot=output_snapshot,
            started_at=started_at,
        )
        return DurableToolInvocation(reserved=True)

    async def append_step(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        step_type: str,
        status: str,
        graph_name: str | None = None,
        node_name: str | None = None,
        tool_name: str | None = None,
        input_snapshot: dict[str, Any] | None = None,
        output_snapshot: dict[str, Any] | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        retry_count: int = 0,
        latency_ms: int = 0,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentRunStep:
        """Append one ordered step in the caller-owned transaction.

        Locking the tenant-scoped parent run before reading the maximum sequence
        serializes every writer that uses this method. The caller decides when to
        commit so a run-state transition and its emitted step can be atomic.
        Snapshots are stored as supplied; callers own redaction and size limits.
        """

        locked_run_id = await self._session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.company_id == company_id,
                AgentRun.id == run_id,
            )
            .with_for_update()
        )
        if locked_run_id is None:
            raise AgentRunStepParentNotFoundError

        return await self._append_locked_step(
            company_id=company_id,
            run_id=run_id,
            step_type=step_type,
            graph_name=graph_name,
            node_name=node_name,
            tool_name=tool_name,
            status=status,
            input_snapshot=dict(input_snapshot or {}),
            output_snapshot=(dict(output_snapshot) if output_snapshot is not None else None),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            retry_count=retry_count,
            latency_ms=latency_ms,
            started_at=started_at,
            completed_at=completed_at,
            error_code=error_code,
            error_message=error_message,
        )

    async def _append_locked_step(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        **values: Any,
    ) -> AgentRunStep:
        """Insert using a fresh statement snapshot after the parent lock is held.

        The maximum sequence must not share the parent's locking statement: a
        concurrent writer may commit while that statement waits for the lock.
        Combining only the subsequent MAX and INSERT saves a round trip without
        changing serialization, rollback, or caller-owned commit boundaries.
        """

        await self._session.flush()
        next_sequence = (
            select(func.coalesce(func.max(AgentRunStep.sequence), 0) + 1)
            .where(
                AgentRunStep.company_id == company_id,
                AgentRunStep.agent_run_id == run_id,
            )
            .scalar_subquery()
        )
        step = await self._session.scalar(
            insert(AgentRunStep)
            .values(
                company_id=company_id,
                agent_run_id=run_id,
                sequence=next_sequence,
                **values,
            )
            .returning(AgentRunStep)
        )
        if step is None:  # pragma: no cover - INSERT RETURNING invariant
            raise RuntimeError("Agent step insertion returned no record.")
        return step

    async def list_steps_after(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[AgentRunStep]:
        """Return a bounded, tenant-scoped page strictly after an SSE cursor."""

        if after_sequence < 0:
            raise ValueError("after_sequence must be nonnegative")
        if not 1 <= limit <= MAX_AGENT_RUN_STEP_PAGE_SIZE:
            raise ValueError(
                f"limit must be between 1 and {MAX_AGENT_RUN_STEP_PAGE_SIZE}"
            )
        result = await self._session.scalars(
            select(AgentRunStep)
            .where(
                AgentRunStep.company_id == company_id,
                AgentRunStep.agent_run_id == run_id,
                AgentRunStep.sequence > after_sequence,
            )
            .order_by(AgentRunStep.sequence)
            .limit(limit)
        )
        return list(result.all())

    async def has_steps(self, *, company_id: UUID, run_id: UUID) -> bool:
        """Return whether the tenant-scoped run has any persisted steps."""

        step_id = await self._session.scalar(
            select(AgentRunStep.id)
            .where(
                AgentRunStep.company_id == company_id,
                AgentRunStep.agent_run_id == run_id,
            )
            .limit(1)
        )
        return step_id is not None

    async def latest_interrupt(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
    ) -> AgentRunStep | None:
        """Return the newest persisted interrupt for a tenant-scoped run."""

        return await self._session.scalar(
            select(AgentRunStep)
            .where(
                AgentRunStep.company_id == company_id,
                AgentRunStep.agent_run_id == run_id,
                AgentRunStep.step_type == "interrupt",
            )
            .order_by(AgentRunStep.sequence.desc())
            .limit(1)
        )

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
