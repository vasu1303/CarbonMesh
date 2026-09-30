from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.core import Actor, Company, ReportingPeriod, Site
from app.db.models.procurement import Supplier
from app.db.models.semantic import MetricDefinition


class SemanticRepositoryProtocol(Protocol):
    async def list_metrics(
        self, company_id: UUID, *, active_only: bool = True
    ) -> Sequence[MetricDefinition]: ...

    async def get_company(self, company_id: UUID) -> Company | None: ...

    async def get_site(self, company_id: UUID, site_id: UUID) -> Site | None: ...

    async def get_reporting_period(
        self, company_id: UUID, reporting_period_id: UUID
    ) -> ReportingPeriod | None: ...

    async def get_actor(self, company_id: UUID, actor_id: UUID) -> Actor | None: ...

    async def get_metrics(
        self, company_id: UUID, metric_definition_ids: Sequence[UUID]
    ) -> Sequence[MetricDefinition]: ...

    async def get_suppliers(
        self, company_id: UUID, supplier_ids: Sequence[UUID]
    ) -> Sequence[Supplier]: ...


class SemanticRepository:
    """Persistence operations used by semantic queries and context resolution."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_metrics(
        self, company_id: UUID, *, active_only: bool = True
    ) -> Sequence[MetricDefinition]:
        statement = select(MetricDefinition).where(MetricDefinition.company_id == company_id)
        if active_only:
            statement = statement.where(MetricDefinition.is_active.is_(True))
        statement = statement.order_by(
            MetricDefinition.key,
            MetricDefinition.version,
            MetricDefinition.id,
        )
        result = await self._session.scalars(statement)
        return result.all()

    async def get_company(self, company_id: UUID) -> Company | None:
        return await self._session.scalar(select(Company).where(Company.id == company_id))

    async def get_site(self, company_id: UUID, site_id: UUID) -> Site | None:
        return await self._session.scalar(
            select(Site).where(Site.company_id == company_id, Site.id == site_id)
        )

    async def get_reporting_period(
        self, company_id: UUID, reporting_period_id: UUID
    ) -> ReportingPeriod | None:
        return await self._session.scalar(
            select(ReportingPeriod).where(
                ReportingPeriod.company_id == company_id,
                ReportingPeriod.id == reporting_period_id,
            )
        )

    async def get_actor(self, company_id: UUID, actor_id: UUID) -> Actor | None:
        return await self._session.scalar(
            select(Actor).where(Actor.company_id == company_id, Actor.id == actor_id)
        )

    async def get_metrics(
        self, company_id: UUID, metric_definition_ids: Sequence[UUID]
    ) -> Sequence[MetricDefinition]:
        if not metric_definition_ids:
            return []
        result = await self._session.scalars(
            select(MetricDefinition).where(
                MetricDefinition.company_id == company_id,
                MetricDefinition.id.in_(metric_definition_ids),
            )
        )
        return result.all()

    async def get_suppliers(
        self, company_id: UUID, supplier_ids: Sequence[UUID]
    ) -> Sequence[Supplier]:
        if not supplier_ids:
            return []
        result = await self._session.scalars(
            select(Supplier).where(
                Supplier.company_id == company_id,
                Supplier.id.in_(supplier_ids),
            )
        )
        return result.all()
