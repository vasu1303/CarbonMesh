from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.core import (
    Actor,
    Approval,
    DataSource,
    EvidenceItem,
    Site,
    SourceDocument,
)
from app.db.models.dispatch import (
    DispatchRecommendation,
    DispatchScenario,
    FlexibleLoad,
    GridForecast,
    OperatingConstraint,
)
from app.db.models.ledger import FactBinding, LedgerEvent, LedgerEventEvidence
from app.db.models.semantic import MethodDefinition, PolicyDefinition

FORECAST_EXTERNAL_REFERENCE = "electricity-maps:forecast:v4"


@dataclass(frozen=True, slots=True)
class LoadRecord:
    load: FlexibleLoad
    constraints: list[OperatingConstraint]


@dataclass(frozen=True, slots=True)
class ScenarioDependencies:
    site: Site
    load: FlexibleLoad
    constraints: list[OperatingConstraint]
    method: MethodDefinition
    policy: PolicyDefinition | None
    source_document: SourceDocument
    requester: Actor


@dataclass(frozen=True, slots=True)
class ScenarioRecord:
    scenario: DispatchScenario
    load: FlexibleLoad
    constraints: list[OperatingConstraint]
    method: MethodDefinition
    policy: PolicyDefinition | None


@dataclass(frozen=True, slots=True)
class RecommendationRecord:
    recommendation: DispatchRecommendation
    approval: Approval | None
    evidence_links: list[LedgerEventEvidence]
    fact_bindings: list[FactBinding]


class DispatchRepository:
    """Tenant-scoped Dispatch persistence with no optimization policy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_loads(
        self,
        *,
        company_id: UUID,
        site_id: UUID | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[LoadRecord], int]:
        predicates = [FlexibleLoad.company_id == company_id]
        if site_id is not None:
            predicates.append(FlexibleLoad.site_id == site_id)
        if active_only:
            predicates.append(FlexibleLoad.is_active.is_(True))
        total = await self.session.scalar(
            select(func.count()).select_from(FlexibleLoad).where(*predicates)
        )
        loads = list(
            (
                await self.session.scalars(
                    select(FlexibleLoad)
                    .where(*predicates)
                    .order_by(FlexibleLoad.site_id, FlexibleLoad.code, FlexibleLoad.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        if not loads:
            return [], int(total or 0)
        constraints = list(
            (
                await self.session.scalars(
                    select(OperatingConstraint)
                    .where(
                        OperatingConstraint.company_id == company_id,
                        OperatingConstraint.flexible_load_id.in_([item.id for item in loads]),
                        OperatingConstraint.is_active.is_(True),
                    )
                    .order_by(
                        OperatingConstraint.flexible_load_id,
                        OperatingConstraint.code,
                        OperatingConstraint.id,
                    )
                )
            ).all()
        )
        grouped: dict[UUID, list[OperatingConstraint]] = {}
        for constraint in constraints:
            grouped.setdefault(constraint.flexible_load_id, []).append(constraint)
        return [LoadRecord(item, grouped.get(item.id, [])) for item in loads], int(total or 0)

    async def get_site(self, *, company_id: UUID, site_id: UUID) -> Site | None:
        return await self.session.scalar(
            select(Site).where(Site.company_id == company_id, Site.id == site_id)
        )

    async def get_actor(self, *, company_id: UUID, actor_id: UUID) -> Actor | None:
        return await self.session.scalar(
            select(Actor).where(Actor.company_id == company_id, Actor.id == actor_id)
        )

    async def get_forecast_data_source(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        synthetic: bool,
    ) -> DataSource | None:
        return await self.session.scalar(
            select(DataSource)
            .where(
                DataSource.company_id == company_id,
                DataSource.site_id == site_id,
                DataSource.external_reference == FORECAST_EXTERNAL_REFERENCE,
                DataSource.is_synthetic.is_(synthetic),
            )
            .order_by(DataSource.created_at.desc(), DataSource.id.desc())
            .limit(1)
        )

    async def get_document_by_checksum(
        self, *, company_id: UUID, checksum: str
    ) -> SourceDocument | None:
        return await self.session.scalar(
            select(SourceDocument).where(
                SourceDocument.company_id == company_id,
                SourceDocument.checksum == checksum,
            )
        )

    async def get_source_document(
        self, *, company_id: UUID, source_document_id: UUID
    ) -> SourceDocument | None:
        return await self.session.scalar(
            select(SourceDocument).where(
                SourceDocument.company_id == company_id,
                SourceDocument.id == source_document_id,
            )
        )

    async def get_latest_forecast_document(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        zone: str,
        synthetic: bool,
    ) -> SourceDocument | None:
        return await self.session.scalar(
            select(SourceDocument)
            .join(
                GridForecast,
                (GridForecast.company_id == SourceDocument.company_id)
                & (GridForecast.source_document_id == SourceDocument.id),
            )
            .join(
                DataSource,
                (DataSource.company_id == SourceDocument.company_id)
                & (DataSource.id == SourceDocument.data_source_id),
            )
            .where(
                SourceDocument.company_id == company_id,
                GridForecast.site_id == site_id,
                GridForecast.zone == zone,
                DataSource.external_reference == FORECAST_EXTERNAL_REFERENCE,
                DataSource.is_synthetic.is_(synthetic),
            )
            .order_by(
                GridForecast.issued_at.desc(),
                SourceDocument.imported_at.desc(),
                SourceDocument.id.desc(),
            )
            .limit(1)
        )

    async def get_evidence(
        self,
        *,
        company_id: UUID,
        source_document_id: UUID,
        locator: str,
    ) -> EvidenceItem | None:
        return await self.session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.company_id == company_id,
                EvidenceItem.source_document_id == source_document_id,
                EvidenceItem.locator == locator,
            )
        )

    async def list_evidence_items(
        self,
        *,
        company_id: UUID,
        evidence_item_ids: set[UUID],
    ) -> list[EvidenceItem]:
        if not evidence_item_ids:
            return []
        return list(
            (
                await self.session.scalars(
                    select(EvidenceItem)
                    .where(
                        EvidenceItem.company_id == company_id,
                        EvidenceItem.id.in_(evidence_item_ids),
                    )
                    .order_by(EvidenceItem.id)
                )
            ).all()
        )

    async def get_forecast_identity(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        zone: str,
        forecast_for: datetime,
        issued_at: datetime,
    ) -> GridForecast | None:
        return await self.session.scalar(
            select(GridForecast).where(
                GridForecast.company_id == company_id,
                GridForecast.site_id == site_id,
                GridForecast.zone == zone,
                GridForecast.forecast_for == forecast_for,
                GridForecast.issued_at == issued_at,
                GridForecast.temporal_granularity == "hourly",
            )
        )

    async def list_forecast_points(
        self, *, company_id: UUID, source_document_id: UUID
    ) -> list[GridForecast]:
        return list(
            (
                await self.session.scalars(
                    select(GridForecast)
                    .where(
                        GridForecast.company_id == company_id,
                        GridForecast.source_document_id == source_document_id,
                    )
                    .order_by(GridForecast.forecast_for, GridForecast.id)
                )
            ).all()
        )

    async def get_scenario_dependencies(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        load_id: UUID,
        method_id: UUID,
        policy_id: UUID | None,
        source_document_id: UUID,
        requested_by: UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> ScenarioDependencies | None:
        site = await self.get_site(company_id=company_id, site_id=site_id)
        load = await self.session.scalar(
            select(FlexibleLoad).where(
                FlexibleLoad.company_id == company_id,
                FlexibleLoad.id == load_id,
                FlexibleLoad.site_id == site_id,
            )
        )
        method = await self.session.scalar(
            select(MethodDefinition).where(
                MethodDefinition.company_id == company_id,
                MethodDefinition.id == method_id,
            )
        )
        policy = None
        if policy_id is not None:
            policy = await self.session.scalar(
                select(PolicyDefinition).where(
                    PolicyDefinition.company_id == company_id,
                    PolicyDefinition.id == policy_id,
                )
            )
        document = await self.session.scalar(
            select(SourceDocument).where(
                SourceDocument.company_id == company_id,
                SourceDocument.id == source_document_id,
            )
        )
        requester = await self.get_actor(company_id=company_id, actor_id=requested_by)
        if not all((site, load, method, document, requester)) or (
            policy_id is not None and policy is None
        ):
            return None
        constraints = list(
            (
                await self.session.scalars(
                    select(OperatingConstraint)
                    .where(
                        OperatingConstraint.company_id == company_id,
                        OperatingConstraint.flexible_load_id == load_id,
                        OperatingConstraint.is_active.is_(True),
                        (OperatingConstraint.valid_from.is_(None))
                        | (OperatingConstraint.valid_from < window_end),
                        (OperatingConstraint.valid_to.is_(None))
                        | (OperatingConstraint.valid_to > window_start),
                    )
                    .order_by(OperatingConstraint.code, OperatingConstraint.id)
                )
            ).all()
        )
        return ScenarioDependencies(site, load, constraints, method, policy, document, requester)

    async def find_scenario_by_signature(
        self, *, company_id: UUID, analysis_signature: str
    ) -> DispatchScenario | None:
        return await self.session.scalar(
            select(DispatchScenario).where(
                DispatchScenario.company_id == company_id,
                DispatchScenario.analysis_signature == analysis_signature,
            )
        )

    async def get_scenario(
        self,
        *,
        company_id: UUID,
        scenario_id: UUID,
        for_update: bool = False,
    ) -> ScenarioRecord | None:
        statement = (
            select(DispatchScenario, FlexibleLoad, MethodDefinition, PolicyDefinition)
                .join(
                    FlexibleLoad,
                    (FlexibleLoad.company_id == DispatchScenario.company_id)
                    & (FlexibleLoad.id == DispatchScenario.flexible_load_id),
                )
                .join(
                    MethodDefinition,
                    (MethodDefinition.company_id == DispatchScenario.company_id)
                    & (MethodDefinition.id == DispatchScenario.method_definition_id),
                )
                .outerjoin(
                    PolicyDefinition,
                    (PolicyDefinition.company_id == DispatchScenario.company_id)
                    & (PolicyDefinition.id == DispatchScenario.policy_definition_id),
                )
                .where(
                    DispatchScenario.company_id == company_id,
                    DispatchScenario.id == scenario_id,
                )
        )
        if for_update:
            statement = statement.with_for_update(of=DispatchScenario)
        row = (await self.session.execute(statement)).one_or_none()
        if row is None:
            return None
        constraints = list(
            (
                await self.session.scalars(
                    select(OperatingConstraint)
                    .where(
                        OperatingConstraint.company_id == company_id,
                        OperatingConstraint.flexible_load_id == row[1].id,
                    )
                    .order_by(OperatingConstraint.code, OperatingConstraint.id)
                )
            ).all()
        )
        return ScenarioRecord(row[0], row[1], constraints, row[2], row[3])

    async def get_recommendation_for_scenario(
        self, *, company_id: UUID, scenario_id: UUID
    ) -> RecommendationRecord | None:
        recommendation = await self.session.scalar(
            select(DispatchRecommendation)
            .where(
                DispatchRecommendation.company_id == company_id,
                DispatchRecommendation.dispatch_scenario_id == scenario_id,
            )
            .order_by(
                DispatchRecommendation.created_at.desc(),
                DispatchRecommendation.id.desc(),
            )
            .limit(1)
        )
        if recommendation is None:
            return None
        approval = await self.session.scalar(
            select(Approval)
            .where(
                Approval.company_id == company_id,
                Approval.target_type == "dispatch_recommendation",
                Approval.target_id == recommendation.id,
            )
            .order_by(Approval.created_at.desc(), Approval.id.desc())
            .limit(1)
        )
        evidence_links = list(
            (
                await self.session.scalars(
                    select(LedgerEventEvidence)
                    .where(
                        LedgerEventEvidence.company_id == company_id,
                        LedgerEventEvidence.ledger_event_id == recommendation.ledger_event_id,
                    )
                    .order_by(LedgerEventEvidence.evidence_item_id)
                )
            ).all()
        )
        bindings = list(
            (
                await self.session.scalars(
                    select(FactBinding)
                    .where(
                        FactBinding.company_id == company_id,
                        FactBinding.artifact_type == "dispatch_recommendation",
                        FactBinding.artifact_id == recommendation.id,
                    )
                    .order_by(FactBinding.placeholder)
                )
            ).all()
        )
        return RecommendationRecord(recommendation, approval, evidence_links, bindings)

    async def get_forecast_ledger_event(
        self, *, company_id: UUID, source_document_id: UUID
    ) -> LedgerEvent | None:
        return await self.session.scalar(
            select(LedgerEvent)
            .where(
                LedgerEvent.company_id == company_id,
                LedgerEvent.event_type == "dispatch_forecast_synced",
                LedgerEvent.entity_type == "dispatch_forecast_snapshot",
                LedgerEvent.entity_id == source_document_id,
            )
            .order_by(LedgerEvent.created_at.desc())
            .limit(1)
        )

    async def list_ledger_event_evidence_ids(
        self, *, company_id: UUID, ledger_event_id: UUID
    ) -> set[UUID]:
        return set(
            (
                await self.session.scalars(
                    select(LedgerEventEvidence.evidence_item_id).where(
                        LedgerEventEvidence.company_id == company_id,
                        LedgerEventEvidence.ledger_event_id == ledger_event_id,
                    )
                )
            ).all()
        )

    async def get_no_feasible_event(
        self, *, company_id: UUID, scenario_id: UUID
    ) -> LedgerEvent | None:
        return await self.session.scalar(
            select(LedgerEvent)
            .where(
                LedgerEvent.company_id == company_id,
                LedgerEvent.event_type == "dispatch_no_feasible_window",
                LedgerEvent.entity_type == "dispatch_scenario",
                LedgerEvent.entity_id == scenario_id,
            )
            .order_by(LedgerEvent.created_at.desc())
            .limit(1)
        )
