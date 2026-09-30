from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import (
    ActivityRecord,
    AgentRun,
    CarbonMeasurement,
    EmissionCalculation,
)
from app.db.models.core import Actor, ReportingPeriod, Site
from app.db.models.procurement import SupplierProduct
from app.db.models.semantic import MethodDefinition, MetricDefinition


@dataclass(frozen=True, slots=True)
class ResolvedContextReferences:
    actor_role: str
    rows_resolved: int


@dataclass(frozen=True, slots=True)
class MeasurementActivityProductInput:
    measurement: CarbonMeasurement
    activity: ActivityRecord
    product: SupplierProduct


class ContextReferenceNotFoundError(LookupError):
    def __init__(self, entity_type: str) -> None:
        super().__init__(f"{entity_type} was not found in the requested company")
        self.entity_type = entity_type


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
        site_id: UUID | None,
        reporting_period_id: UUID | None,
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

        rows_resolved = 1
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
            rows_resolved += 1

        if reporting_period_id is not None:
            resolved_period_id = await self._session.scalar(
                select(ReportingPeriod.id).where(
                    ReportingPeriod.company_id == company_id,
                    ReportingPeriod.id == reporting_period_id,
                )
            )
            if resolved_period_id is None:
                raise ContextReferenceNotFoundError("reporting_period")
            rows_resolved += 1

        return ResolvedContextReferences(actor_role=actor_role, rows_resolved=rows_resolved)

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

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
