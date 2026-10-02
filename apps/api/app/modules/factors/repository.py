from __future__ import annotations

from uuid import UUID

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import EmissionFactor
from app.db.models.core import Actor, Company, DataSource, EvidenceItem, SourceDocument
from app.db.models.ledger import LedgerEvent
from app.db.models.semantic import MetricDefinition


class FactorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_company(self, company_id: UUID) -> Company | None:
        return await self.session.scalar(
            select(Company)
            .where(
                Company.id == company_id,
                Company.is_active.is_(True),
            )
            .with_for_update()
        )

    async def actor(self, company_id: UUID, actor_id: UUID) -> Actor | None:
        return await self.session.scalar(
            select(Actor).where(
                Actor.company_id == company_id,
                Actor.id == actor_id,
                Actor.is_active.is_(True),
            )
        )

    async def metric(self, company_id: UUID, metric_id: UUID) -> MetricDefinition | None:
        return await self.session.scalar(
            select(MetricDefinition).where(
                MetricDefinition.company_id == company_id,
                MetricDefinition.id == metric_id,
                MetricDefinition.is_active.is_(True),
            )
        )

    async def evidence(
        self, company_id: UUID, evidence_id: UUID
    ) -> Row[tuple[EvidenceItem, SourceDocument, DataSource]] | None:
        return (
            await self.session.execute(
                select(EvidenceItem, SourceDocument, DataSource)
                .join(
                    SourceDocument,
                    (EvidenceItem.company_id == SourceDocument.company_id)
                    & (EvidenceItem.source_document_id == SourceDocument.id),
                )
                .join(
                    DataSource,
                    (SourceDocument.company_id == DataSource.company_id)
                    & (SourceDocument.data_source_id == DataSource.id),
                )
                .where(EvidenceItem.company_id == company_id, EvidenceItem.id == evidence_id)
            )
        ).one_or_none()

    async def find(
        self, company_id: UUID, code: str, version: str, geography: str
    ) -> EmissionFactor | None:
        return await self.session.scalar(
            select(EmissionFactor).where(
                EmissionFactor.company_id == company_id,
                EmissionFactor.factor_code == code,
                EmissionFactor.version == version,
                EmissionFactor.geography == geography,
            )
        )

    async def registration_event(self, company_id: UUID, factor_id: UUID) -> LedgerEvent | None:
        return await self.session.scalar(
            select(LedgerEvent).where(
                LedgerEvent.company_id == company_id,
                LedgerEvent.entity_type == "emission_factor",
                LedgerEvent.entity_id == factor_id,
                LedgerEvent.event_type == "factor.registered",
            )
        )

    async def list(
        self,
        *,
        company_id: UUID,
        material_code: str | None,
        product_code: str | None,
        active: bool | None,
        limit: int,
        offset: int,
    ) -> tuple[list[EmissionFactor], int]:
        predicates = [EmissionFactor.company_id == company_id]
        if material_code is not None:
            predicates.append(EmissionFactor.material_code == material_code)
        if product_code is not None:
            predicates.append(EmissionFactor.product_code == product_code)
        if active is not None:
            predicates.append(
                (EmissionFactor.status == "active")
                if active
                else (EmissionFactor.status != "active")
            )
        total = await self.session.scalar(
            select(func.count()).select_from(EmissionFactor).where(*predicates)
        )
        items = list(
            await self.session.scalars(
                select(EmissionFactor)
                .where(*predicates)
                .order_by(
                    EmissionFactor.factor_code,
                    EmissionFactor.version,
                    EmissionFactor.geography,
                    EmissionFactor.id,
                )
                .limit(limit)
                .offset(offset)
            )
        )
        return items, int(total or 0)

    def add(self, instance: object) -> None:
        self.session.add(instance)

    async def flush(self) -> None:
        await self.session.flush()
