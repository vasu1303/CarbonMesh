"""Tenant-scoped agent telemetry aggregation and documented footprint proxies."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import Settings, get_settings
from app.db.models.ai import AgentRun, AgentRunStep
from app.db.models.core import Approval
from app.db.models.dispatch import DispatchRecommendation, DispatchScenario
from app.db.models.ledger import LedgerEvent
from app.db.models.procurement import ProcurementScenario, Recommendation
from app.modules.agents.schemas import (
    AgentSustainabilityMetrics,
    SustainabilityAssumptions,
    SustainabilityBenefitFact,
)
from app.modules.ledger.service import payload_sha256

SUSTAINABILITY_PROXY_METHOD = "token_energy_proxy"
SUSTAINABILITY_PROXY_VERSION = "1.0"
SUSTAINABILITY_CAVEAT = (
    "Estimated energy and CO2e are documented proxies derived from token usage; "
    "tokens are telemetry and are not direct measurements of energy or carbon emissions."
)
MAX_METRIC_RUNS = 10_000


class SustainabilityIntegrityError(RuntimeError):
    """A projected benefit no longer matches its approved immutable ledger facts."""


def _verified_benefit(
    recommendation: Recommendation | DispatchRecommendation,
    event: LedgerEvent,
    approval: Approval,
    decision: LedgerEvent,
    *,
    target_type: str,
) -> SustainabilityBenefitFact:
    expected_entity, expected_event = (
        ("recommendation", "recommendation.created")
        if target_type == "procurement_recommendation"
        else ("dispatch_recommendation", "dispatch_recommendation_created")
    )
    if (
        not isinstance(event.payload, dict) or not isinstance(decision.payload, dict)
        or event.entity_id != recommendation.id or event.entity_type != expected_entity
        or event.event_type != expected_event
        or payload_sha256(event.payload)[1] != event.payload_hash
        or event.payload_hash != recommendation.payload_hash
        or event.payload_hash != approval.preview_hash
        or event.analysis_signature != recommendation.analysis_signature
        or event.analysis_signature != approval.analysis_signature
        or decision.entity_type != "approval" or decision.entity_id != approval.id
        or decision.event_type != "approval.approved"
        or payload_sha256(decision.payload)[1] != decision.payload_hash
        or decision.payload.get("decision") != "approved"
        or decision.payload.get("target_type") != target_type
        or decision.payload.get("target_id") != str(recommendation.id)
        or decision.payload.get("preview_hash") != event.payload_hash
        or decision.payload.get("analysis_signature") != event.analysis_signature
        or decision.created_by != approval.decided_by
    ):
        raise SustainabilityIntegrityError
    try:
        avoided = Decimal(str(event.payload["avoided_kgco2e"]))
    except (KeyError, InvalidOperation, ValueError):
        raise SustainabilityIntegrityError from None
    if not avoided.is_finite() or avoided != recommendation.avoided_kgco2e:
        raise SustainabilityIntegrityError
    return SustainabilityBenefitFact(
        target_type=target_type, target_id=recommendation.id,
        ledger_event_id=event.id, avoided_kgco2e=avoided,
    )


def estimate_sustainability_proxy(
    *,
    input_tokens: int,
    output_tokens: int,
    settings: Settings | None = None,
) -> tuple[Decimal, Decimal]:
    configured = settings or get_settings()
    token_count = Decimal(input_tokens + output_tokens)
    energy_wh = token_count / Decimal(1000) * configured.ai_energy_wh_per_1k_tokens
    co2e_g = energy_wh / Decimal(1000) * configured.ai_grid_intensity_gco2e_per_kwh
    return energy_wh.quantize(Decimal("0.000000001")), co2e_g.quantize(
        Decimal("0.000000001")
    )


class AgentSustainabilityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_runs(
        self,
        *,
        company_id: UUID,
        from_time: datetime | None,
        to_time: datetime | None,
        workflow: str | None,
    ) -> list[AgentRun]:
        statement = select(AgentRun).where(AgentRun.company_id == company_id)
        if from_time is not None:
            statement = statement.where(AgentRun.started_at >= from_time)
        if to_time is not None:
            statement = statement.where(AgentRun.started_at < to_time)
        if workflow is not None:
            statement = statement.where(AgentRun.workflow == workflow)
        statement = statement.order_by(AgentRun.started_at.desc()).limit(MAX_METRIC_RUNS)
        return list((await self._session.scalars(statement)).all())

    async def list_provider_steps(
        self,
        *,
        company_id: UUID,
        run_ids: list[UUID],
    ) -> list[AgentRunStep]:
        if not run_ids:
            return []
        return list(
            (
                await self._session.scalars(
                    select(AgentRunStep).where(
                        AgentRunStep.company_id == company_id,
                        AgentRunStep.agent_run_id.in_(run_ids),
                        AgentRunStep.step_type == "provider",
                    )
                )
            ).all()
        )

    async def list_benefit_facts(
        self, *, company_id: UUID, run_ids: list[UUID],
    ) -> list[SustainabilityBenefitFact]:
        if not run_ids:
            return []
        facts = {}
        decision = aliased(LedgerEvent)
        for model, scenario, foreign_key, target_type in (
            (Recommendation, ProcurementScenario, Recommendation.scenario_id,
             "procurement_recommendation"),
            (DispatchRecommendation, DispatchScenario, DispatchRecommendation.dispatch_scenario_id,
             "dispatch_recommendation"),
        ):
            rows = (await self._session.execute(
                select(model, LedgerEvent, Approval, decision).select_from(model)
                .join(scenario, foreign_key == scenario.id)
                .join(LedgerEvent, model.ledger_event_id == LedgerEvent.id)
                .join(Approval, (Approval.target_id == model.id)
                      & (Approval.company_id == model.company_id)
                      & (Approval.target_type == target_type))
                .join(decision, (Approval.ledger_event_id == decision.id)
                      & (decision.company_id == model.company_id))
                .where(
                    model.company_id == company_id, scenario.company_id == company_id,
                    LedgerEvent.company_id == company_id,
                    model.status == "approved", model.invalidated_at.is_(None),
                    Approval.status == "approved",
                    or_(scenario.agent_run_id.in_(run_ids), LedgerEvent.agent_run_id.in_(run_ids)),
                )
            )).all()
            for recommendation, event, approved, decision_event in rows:
                fact = _verified_benefit(
                    recommendation, event, approved, decision_event, target_type=target_type,
                )
                facts[(target_type, recommendation.id)] = fact
        return sorted(facts.values(), key=lambda fact: (fact.target_type, str(fact.target_id)))


class AgentSustainabilityService:
    def __init__(
        self,
        repository: AgentSustainabilityRepository,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings or get_settings()

    async def get_metrics(
        self,
        *,
        company_id: UUID,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        workflow: str | None = None,
        provider: str | None = None,
    ) -> AgentSustainabilityMetrics:
        for value in (from_time, to_time):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Agent metric timestamps must include a UTC offset.")
        if from_time is not None and to_time is not None and to_time <= from_time:
            raise ValueError("to_time must be after from_time.")
        runs = await self._repository.list_runs(
            company_id=company_id,
            from_time=from_time,
            to_time=to_time,
            workflow=workflow,
        )
        if provider is not None:
            runs = [run for run in runs if (run.telemetry or {}).get("provider") == provider]
        steps = await self._repository.list_provider_steps(
            company_id=company_id,
            run_ids=[run.id for run in runs],
        )

        cached_input_tokens = 0
        cache_hits = sum(int((run.telemetry or {}).get("cache_hits", 0)) for run in runs)
        for step in steps:
            snapshot = step.output_snapshot or {}
            data = snapshot.get("data")
            if not isinstance(data, dict):
                data = snapshot
            cached = data.get("cached_input_tokens", 0)
            if (
                snapshot.get("event_name") == "provider.completed"
                and isinstance(cached, int)
                and not isinstance(cached, bool)
                and cached >= 0
            ):
                cached_input_tokens += cached
            if snapshot.get("event_name") == "provider.cache_hit" or data.get("cache_hit") is True:
                cache_hits += 1

        input_tokens = sum(run.input_tokens for run in runs)
        output_tokens = sum(run.output_tokens for run in runs)
        estimated_energy = sum(
            (run.estimated_energy_wh for run in runs if run.estimated_energy_wh is not None),
            Decimal(0),
        )
        estimated_co2e = sum(
            (run.estimated_co2e_g for run in runs if run.estimated_co2e_g is not None),
            Decimal(0),
        )
        benefit_facts = await self._repository.list_benefit_facts(
            company_id=company_id, run_ids=[run.id for run in runs],
        )
        benefit = sum((fact.avoided_kgco2e for fact in benefit_facts), Decimal(0))
        assumption_sets = {}
        assumption_coverage = 0
        for run in runs:
            stored = (run.telemetry or {}).get("sustainability_assumptions")
            if not isinstance(stored, dict):
                continue
            try:
                assumptions = SustainabilityAssumptions.model_validate({
                    **stored, "caveat": SUSTAINABILITY_CAVEAT,
                })
            except ValidationError:
                continue
            assumption_coverage += 1
            assumption_sets[assumptions.model_dump_json()] = assumptions
        complete_proxy = (
            bool(runs) and assumption_coverage == len(runs) and len(assumption_sets) == 1
            and all(run.estimated_co2e_g is not None for run in runs)
        )
        return AgentSustainabilityMetrics(
            company_id=company_id,
            from_time=from_time,
            to_time=to_time,
            run_count=len(runs),
            completed_run_count=sum(
                run.terminal_state in {"success", "completed"} for run in runs
            ),
            interrupted_run_count=sum(
                run.terminal_state in {"needs_clarification", "approval_required"}
                for run in runs
            ),
            provider_call_count=sum(run.model_calls for run in runs),
            tool_call_count=sum(run.tool_calls for run in runs),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            retry_count=sum(run.retry_count for run in runs),
            cache_hits=cache_hits,
            latency_ms=sum(run.latency_ms for run in runs),
            estimated_energy_wh=estimated_energy,
            estimated_co2e_g=estimated_co2e,
            proxy_coverage_runs=sum(run.estimated_energy_wh is not None for run in runs),
            assumptions=(next(iter(assumption_sets.values())) if len(assumption_sets) == 1
                         else self.assumptions),
            external_api_calls=sum(run.api_calls for run in runs),
            business_benefit_kgco2e=benefit,
            benefit_to_footprint_ratio=(
                (benefit * Decimal(1000) / estimated_co2e).quantize(Decimal("0.000001"))
                if complete_proxy and estimated_co2e > 0 else None
            ),
            benefit_facts=benefit_facts,
            assumption_coverage_runs=assumption_coverage,
            assumption_sets=list(assumption_sets.values()),
        )

    @property
    def assumptions(self) -> SustainabilityAssumptions:
        return SustainabilityAssumptions(
            method=SUSTAINABILITY_PROXY_METHOD,
            version=SUSTAINABILITY_PROXY_VERSION,
            energy_wh_per_1k_tokens=self._settings.ai_energy_wh_per_1k_tokens,
            grid_intensity_gco2e_per_kwh=(
                self._settings.ai_grid_intensity_gco2e_per_kwh
            ),
            caveat=SUSTAINABILITY_CAVEAT,
        )
