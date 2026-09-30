from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from typing import Literal
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models.carbon import CarbonMeasurement
from app.db.models.core import Actor, EvidenceItem
from app.db.models.procurement import (
    Approval,
    ProcurementScenario,
    Recommendation,
    Supplier,
    SupplierProduct,
    SupplierScore,
)
from app.db.models.semantic import MethodDefinition
from app.modules.procurement.repository import ProductRecord
from app.modules.procurement.review import RecommendationReviewInputs

PREVIEW_CANDIDATE_LIMIT = 100


def _approval_filters(
    *,
    company_id: UUID,
    status: Literal["pending", "approved", "rejected"] | None,
) -> list[object]:
    filters: list[object] = [Approval.company_id == company_id]
    if status is not None:
        filters.append(Approval.status == status)
    return filters


async def list_approval_records(
    session: AsyncSession,
    *,
    company_id: UUID,
    status: Literal["pending", "approved", "rejected"] | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[Approval, Recommendation, Actor, Actor | None]], int]:
    requester = aliased(Actor)
    decider = aliased(Actor)
    filters = _approval_filters(company_id=company_id, status=status)

    query: Select = (
        select(Approval, Recommendation, requester, decider)
        .join(
            Recommendation,
            (Recommendation.company_id == Approval.company_id)
            & (Recommendation.id == Approval.recommendation_id),
        )
        .join(
            requester,
            (requester.company_id == Approval.company_id) & (requester.id == Approval.requested_by),
        )
        .outerjoin(
            decider,
            (decider.company_id == Approval.company_id) & (decider.id == Approval.decided_by),
        )
        .where(*filters)
        .order_by(Approval.created_at.desc(), Approval.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = await session.execute(query)
    total = await session.scalar(select(func.count()).select_from(Approval).where(*filters))
    return list(rows), int(total or 0)


async def load_recommendation_review_inputs(
    session: AsyncSession,
    *,
    company_id: UUID,
    recommendations: Collection[Recommendation],
) -> dict[UUID, RecommendationReviewInputs]:
    """Load integrity inputs for an approval page with a fixed number of queries."""
    page = [item for item in recommendations if item.company_id == company_id]
    if not page:
        return {}

    scenario_ids = {item.scenario_id for item in page}
    scenarios = list(
        await session.scalars(
            select(ProcurementScenario).where(
                ProcurementScenario.company_id == company_id,
                ProcurementScenario.id.in_(scenario_ids),
            )
        )
    )
    scenarios_by_id = {item.id: item for item in scenarios}

    reviewed_product_ids = {
        product_id
        for item in page
        for product_id in (item.baseline_product_id, item.recommended_product_id)
    }
    product_rows = (
        await session.execute(
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
                SupplierProduct.id.in_(reviewed_product_ids),
            )
        )
    ).all()
    products_by_id = {
        row[0].id: ProductRecord(product=row[0], supplier=row[1], evidence=row[2])
        for row in product_rows
    }

    selected_score_ids = {item.supplier_score_id for item in page}
    score_rows = (
        await session.execute(
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
                or_(
                    SupplierScore.scenario_id.in_(scenario_ids),
                    SupplierScore.id.in_(selected_score_ids),
                ),
            )
            .order_by(
                SupplierScore.scenario_id,
                SupplierScore.rank.asc().nulls_last(),
                SupplierScore.total_score.desc(),
                SupplierProduct.id,
            )
        )
    ).all()
    scores_by_id: dict[UUID, SupplierScore] = {}
    assessments_by_scenario: defaultdict[UUID, list[tuple[SupplierScore, ProductRecord]]] = (
        defaultdict(list)
    )
    for score, product, supplier, evidence in score_rows:
        product_record = ProductRecord(
            product=product,
            supplier=supplier,
            evidence=evidence,
        )
        scores_by_id[score.id] = score
        if score.scenario_id in scenario_ids:
            assessments_by_scenario[score.scenario_id].append((score, product_record))

    measurement_ids = {item.carbon_measurement_id for item in scenarios}
    measurements = list(
        await session.scalars(
            select(CarbonMeasurement).where(
                CarbonMeasurement.company_id == company_id,
                CarbonMeasurement.id.in_(measurement_ids),
            )
        )
    )
    measurements_by_id = {item.id: item for item in measurements}

    method_ids = {item.method_definition_id for item in scenarios}
    methods = list(
        await session.scalars(
            select(MethodDefinition).where(
                MethodDefinition.company_id == company_id,
                MethodDefinition.id.in_(method_ids),
            )
        )
    )
    methods_by_id = {item.id: item for item in methods}

    categories = {
        product.product.category
        for item in page
        if (product := products_by_id.get(item.baseline_product_id)) is not None
    }
    eligible_by_category: defaultdict[str, list[ProductRecord]] = defaultdict(list)
    if categories:
        candidate_rank = (
            func.row_number()
            .over(
                partition_by=SupplierProduct.category,
                order_by=(SupplierProduct.product_code, SupplierProduct.id),
            )
            .label("candidate_rank")
        )
        ranked_candidates = (
            select(
                SupplierProduct.company_id.label("company_id"),
                SupplierProduct.id.label("product_id"),
                candidate_rank,
            )
            .join(
                Supplier,
                (Supplier.company_id == SupplierProduct.company_id)
                & (Supplier.id == SupplierProduct.supplier_id),
            )
            .where(
                SupplierProduct.company_id == company_id,
                SupplierProduct.category.in_(categories),
                SupplierProduct.is_active.is_(True),
                Supplier.status == "active",
            )
            .subquery()
        )
        candidate_rows = (
            await session.execute(
                select(SupplierProduct, Supplier, EvidenceItem)
                .join(
                    ranked_candidates,
                    (ranked_candidates.c.company_id == SupplierProduct.company_id)
                    & (ranked_candidates.c.product_id == SupplierProduct.id),
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
                .where(ranked_candidates.c.candidate_rank <= PREVIEW_CANDIDATE_LIMIT + 1)
                .order_by(SupplierProduct.category, ranked_candidates.c.candidate_rank)
            )
        ).all()
        for product, supplier, evidence in candidate_rows:
            eligible_by_category[product.category].append(
                ProductRecord(product=product, supplier=supplier, evidence=evidence)
            )

    result: dict[UUID, RecommendationReviewInputs] = {}
    for recommendation in page:
        scenario = scenarios_by_id.get(recommendation.scenario_id)
        baseline = products_by_id.get(recommendation.baseline_product_id)
        selected = products_by_id.get(recommendation.recommended_product_id)
        score = scores_by_id.get(recommendation.supplier_score_id)
        if scenario is None or baseline is None or selected is None or score is None:
            continue
        measurement = measurements_by_id.get(scenario.carbon_measurement_id)
        method = methods_by_id.get(scenario.method_definition_id)
        if measurement is None or method is None:
            continue
        eligible_candidates = [
            item
            for item in eligible_by_category[baseline.product.category]
            if item.product.id != scenario.current_product_id
        ][:PREVIEW_CANDIDATE_LIMIT]
        result[recommendation.id] = RecommendationReviewInputs(
            scenario=scenario,
            baseline=baseline,
            selected=selected,
            score=score,
            method=method,
            measurement=measurement,
            candidate_assessments=assessments_by_scenario[scenario.id],
            eligible_candidates=eligible_candidates,
        )
    return result


async def get_approval_for_update(
    session: AsyncSession,
    *,
    company_id: UUID,
    approval_id: UUID,
) -> Approval | None:
    return await session.scalar(
        select(Approval)
        .where(
            Approval.company_id == company_id,
            Approval.id == approval_id,
        )
        .with_for_update()
    )


async def get_recommendation_for_update(
    session: AsyncSession,
    *,
    company_id: UUID,
    recommendation_id: UUID,
) -> Recommendation | None:
    return await session.scalar(
        select(Recommendation)
        .where(
            Recommendation.company_id == company_id,
            Recommendation.id == recommendation_id,
        )
        .with_for_update()
    )


async def get_actor(
    session: AsyncSession,
    *,
    company_id: UUID,
    actor_id: UUID,
) -> Actor | None:
    return await session.scalar(
        select(Actor).where(Actor.company_id == company_id, Actor.id == actor_id)
    )


async def get_pending_approval(
    session: AsyncSession,
    *,
    company_id: UUID,
    recommendation_id: UUID,
) -> Approval | None:
    return await session.scalar(
        select(Approval).where(
            Approval.company_id == company_id,
            Approval.recommendation_id == recommendation_id,
            Approval.status == "pending",
        )
    )
