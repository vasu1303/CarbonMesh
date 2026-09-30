from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import CarbonMeasurement
from app.db.models.core import Actor, EvidenceItem, ReportingPeriod, Site
from app.db.models.procurement import (
    Approval,
    FactBinding,
    ProcurementScenario,
    Recommendation,
    Supplier,
    SupplierProduct,
    SupplierScore,
)
from app.db.models.semantic import MethodDefinition


@dataclass(frozen=True, slots=True)
class ProductRecord:
    product: SupplierProduct
    supplier: Supplier
    evidence: EvidenceItem | None = None


@dataclass(frozen=True, slots=True)
class ScenarioDependencies:
    site: Site
    period: ReportingPeriod
    current_product: ProductRecord
    measurement: CarbonMeasurement
    method: MethodDefinition
    requested_by: Actor


class ProcurementRepository:
    """Persistence operations only; deterministic policy lives in the service layer."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_products(
        self,
        *,
        company_id: UUID,
        material_code: str | None,
        category: str | None,
        active_only: bool,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProductRecord], int]:
        predicates = [SupplierProduct.company_id == company_id]
        if material_code:
            predicates.append(SupplierProduct.material_code == material_code)
        if category:
            predicates.append(SupplierProduct.category == category)
        if active_only:
            predicates.extend([SupplierProduct.is_active.is_(True), Supplier.status == "active"])
        if search:
            pattern = f"%{search.strip()}%"
            predicates.append(
                or_(
                    SupplierProduct.name.ilike(pattern),
                    SupplierProduct.product_code.ilike(pattern),
                    Supplier.name.ilike(pattern),
                )
            )

        base = (
            select(SupplierProduct, Supplier)
            .join(
                Supplier,
                (Supplier.company_id == SupplierProduct.company_id)
                & (Supplier.id == SupplierProduct.supplier_id),
            )
            .where(*predicates)
        )
        total = await self.session.scalar(
            select(func.count()).select_from(base.order_by(None).subquery())
        )
        rows = (
            await self.session.execute(
                base.order_by(Supplier.name, SupplierProduct.name, SupplierProduct.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [ProductRecord(product=row[0], supplier=row[1]) for row in rows], int(total or 0)

    async def get_product(
        self,
        *,
        company_id: UUID,
        product_id: UUID,
    ) -> ProductRecord | None:
        statement = (
            select(SupplierProduct, Supplier, EvidenceItem)
            .join(
                Supplier,
                (Supplier.company_id == SupplierProduct.company_id)
                & (Supplier.id == SupplierProduct.supplier_id),
            )
            .outerjoin(
                EvidenceItem,
                (EvidenceItem.company_id == SupplierProduct.company_id)
                & (EvidenceItem.id == SupplierProduct.evidence_item_id),
            )
            .where(
                SupplierProduct.company_id == company_id,
                SupplierProduct.id == product_id,
            )
        )
        row = (await self.session.execute(statement)).one_or_none()
        if row is None:
            return None
        return ProductRecord(
            product=row[0],
            supplier=row[1],
            evidence=row[2],
        )

    async def get_scenario_dependencies(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        reporting_period_id: UUID,
        current_product_id: UUID,
        carbon_measurement_id: UUID,
        method_definition_id: UUID,
        requested_by: UUID,
    ) -> ScenarioDependencies | None:
        site = await self.session.scalar(
            select(Site).where(Site.company_id == company_id, Site.id == site_id)
        )
        period = await self.session.scalar(
            select(ReportingPeriod).where(
                ReportingPeriod.company_id == company_id,
                ReportingPeriod.id == reporting_period_id,
            )
        )
        product = await self.get_product(
            company_id=company_id,
            product_id=current_product_id,
        )
        measurement = await self.session.scalar(
            select(CarbonMeasurement).where(
                CarbonMeasurement.company_id == company_id,
                CarbonMeasurement.id == carbon_measurement_id,
            )
        )
        method = await self.session.scalar(
            select(MethodDefinition).where(
                MethodDefinition.company_id == company_id,
                MethodDefinition.id == method_definition_id,
            )
        )
        actor = await self.session.scalar(
            select(Actor).where(Actor.company_id == company_id, Actor.id == requested_by)
        )
        if not all((site, period, product, measurement, method, actor)):
            return None
        return ScenarioDependencies(
            site=site,
            period=period,
            current_product=product,
            measurement=measurement,
            method=method,
            requested_by=actor,
        )

    async def find_scenario_by_signature(
        self, *, company_id: UUID, analysis_signature: str
    ) -> ProcurementScenario | None:
        return await self.session.scalar(
            select(ProcurementScenario).where(
                ProcurementScenario.company_id == company_id,
                ProcurementScenario.analysis_signature == analysis_signature,
            )
        )

    async def get_scenario(
        self, *, company_id: UUID, scenario_id: UUID
    ) -> ProcurementScenario | None:
        return await self.session.scalar(
            select(ProcurementScenario).where(
                ProcurementScenario.company_id == company_id,
                ProcurementScenario.id == scenario_id,
            )
        )

    async def get_period(
        self, *, company_id: UUID, reporting_period_id: UUID
    ) -> ReportingPeriod | None:
        return await self.session.scalar(
            select(ReportingPeriod).where(
                ReportingPeriod.company_id == company_id,
                ReportingPeriod.id == reporting_period_id,
            )
        )

    async def get_measurement(
        self, *, company_id: UUID, measurement_id: UUID
    ) -> CarbonMeasurement | None:
        return await self.session.scalar(
            select(CarbonMeasurement).where(
                CarbonMeasurement.company_id == company_id,
                CarbonMeasurement.id == measurement_id,
            )
        )

    async def get_method(self, *, company_id: UUID, method_id: UUID) -> MethodDefinition | None:
        return await self.session.scalar(
            select(MethodDefinition).where(
                MethodDefinition.company_id == company_id,
                MethodDefinition.id == method_id,
            )
        )

    async def list_candidate_products(
        self,
        *,
        company_id: UUID,
        current_product_id: UUID,
        category: str,
        limit: int = 100,
    ) -> list[ProductRecord]:
        rows = (
            await self.session.execute(
                select(SupplierProduct, Supplier, EvidenceItem)
                .join(
                    Supplier,
                    (Supplier.company_id == SupplierProduct.company_id)
                    & (Supplier.id == SupplierProduct.supplier_id),
                )
                .outerjoin(
                    EvidenceItem,
                    (EvidenceItem.company_id == SupplierProduct.company_id)
                    & (EvidenceItem.id == SupplierProduct.evidence_item_id),
                )
                .where(
                    SupplierProduct.company_id == company_id,
                    SupplierProduct.id != current_product_id,
                    SupplierProduct.category == category,
                    SupplierProduct.is_active.is_(True),
                    Supplier.status == "active",
                )
                .order_by(SupplierProduct.product_code, SupplierProduct.id)
                .limit(limit)
            )
        ).all()
        return [ProductRecord(product=row[0], supplier=row[1], evidence=row[2]) for row in rows]

    async def list_scores(
        self, *, company_id: UUID, scenario_id: UUID
    ) -> list[tuple[SupplierScore, ProductRecord]]:
        rows = (
            await self.session.execute(
                select(SupplierScore, SupplierProduct, Supplier, EvidenceItem)
                .join(
                    SupplierProduct,
                    (SupplierProduct.company_id == SupplierScore.company_id)
                    & (SupplierProduct.id == SupplierScore.supplier_product_id),
                )
                .join(
                    Supplier,
                    (Supplier.company_id == SupplierProduct.company_id)
                    & (Supplier.id == SupplierProduct.supplier_id),
                )
                .outerjoin(
                    EvidenceItem,
                    (EvidenceItem.company_id == SupplierProduct.company_id)
                    & (EvidenceItem.id == SupplierProduct.evidence_item_id),
                )
                .where(
                    SupplierScore.company_id == company_id,
                    SupplierScore.scenario_id == scenario_id,
                )
                .order_by(
                    SupplierScore.rank.asc().nulls_last(),
                    SupplierScore.total_score.desc(),
                    SupplierProduct.id,
                )
            )
        ).all()
        return [
            (
                row[0],
                ProductRecord(product=row[1], supplier=row[2], evidence=row[3]),
            )
            for row in rows
        ]

    async def get_recommendation_for_scenario(
        self, *, company_id: UUID, scenario_id: UUID
    ) -> Recommendation | None:
        return await self.session.scalar(
            select(Recommendation)
            .where(
                Recommendation.company_id == company_id,
                Recommendation.scenario_id == scenario_id,
            )
            .order_by(Recommendation.created_at.desc())
            .limit(1)
        )

    async def get_recommendation(
        self, *, company_id: UUID, recommendation_id: UUID
    ) -> Recommendation | None:
        return await self.session.scalar(
            select(Recommendation).where(
                Recommendation.company_id == company_id,
                Recommendation.id == recommendation_id,
            )
        )

    async def get_score(self, *, company_id: UUID, score_id: UUID) -> SupplierScore | None:
        return await self.session.scalar(
            select(SupplierScore).where(
                SupplierScore.company_id == company_id,
                SupplierScore.id == score_id,
            )
        )

    async def list_fact_bindings(
        self, *, company_id: UUID, recommendation_id: UUID
    ) -> list[FactBinding]:
        return list(
            (
                await self.session.scalars(
                    select(FactBinding)
                    .where(
                        FactBinding.company_id == company_id,
                        FactBinding.recommendation_id == recommendation_id,
                    )
                    .order_by(FactBinding.placeholder)
                )
            ).all()
        )

    async def get_approval(self, *, company_id: UUID, recommendation_id: UUID) -> Approval | None:
        return await self.session.scalar(
            select(Approval)
            .where(
                Approval.company_id == company_id,
                Approval.recommendation_id == recommendation_id,
            )
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
