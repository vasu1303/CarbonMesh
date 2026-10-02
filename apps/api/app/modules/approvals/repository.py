from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models.assurance import DisclosureDraft
from app.db.models.carbon import CalculationRun, CarbonMeasurement
from app.db.models.core import Actor, Approval, Company, EvidenceItem
from app.db.models.dispatch import DispatchRecommendation
from app.db.models.ledger import FactBinding, LedgerEvent
from app.db.models.procurement import (
    ProcurementScenario,
    Recommendation,
    Supplier,
    SupplierProduct,
    SupplierScore,
)
from app.db.models.semantic import MethodDefinition
from app.modules.approvals.schemas import ApprovalStatus
from app.modules.procurement.repository import ProductRecord
from app.modules.procurement.review import RecommendationReviewInputs

PREVIEW_CANDIDATE_LIMIT = 100


def _approval_filters(
    *,
    company_id: UUID,
    status: ApprovalStatus | None,
) -> list[object]:
    filters: list[object] = [Approval.company_id == company_id]
    if status is not None:
        filters.append(Approval.status == status)
    return filters


async def list_approval_records(
    session: AsyncSession,
    *,
    company_id: UUID,
    status: ApprovalStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[Approval, Recommendation | None, Actor, Actor | None]], int]:
    requester = aliased(Actor)
    decider = aliased(Actor)
    filters = _approval_filters(company_id=company_id, status=status)

    query: Select = (
        select(Approval, Recommendation, requester, decider)
        .outerjoin(
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


async def get_approval_record(
    session: AsyncSession, *, company_id: UUID, approval_id: UUID
) -> tuple[Approval, Recommendation | None, Actor, Actor | None] | None:
    requester, decider = aliased(Actor), aliased(Actor)
    result = await session.execute(
        select(Approval, Recommendation, requester, decider)
        .outerjoin(
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
        .where(Approval.company_id == company_id, Approval.id == approval_id)
    )
    return result.one_or_none()


async def get_generic_target(
    session: AsyncSession, *, approval: Approval, for_update: bool = False
) -> DisclosureDraft | DispatchRecommendation | None:
    model = {
        "disclosure_draft": DisclosureDraft,
        "dispatch_recommendation": DispatchRecommendation,
    }.get(approval.target_type)
    if model is None or approval.target_id is None:
        return None
    query = select(model).where(
        model.company_id == approval.company_id, model.id == approval.target_id
    )
    if for_update:
        query = query.with_for_update()
    return await session.scalar(query)


async def get_ledger_event(
    session: AsyncSession, *, company_id: UUID, event_id: UUID
) -> LedgerEvent | None:
    return await session.scalar(
        select(LedgerEvent).where(LedgerEvent.company_id == company_id, LedgerEvent.id == event_id)
    )


async def list_fact_bindings(
    session: AsyncSession,
    *,
    company_id: UUID,
    artifact_type: str,
    artifact_ids: Collection[UUID],
) -> dict[UUID, list[FactBinding]]:
    if not artifact_ids:
        return {}
    rows = await session.scalars(
        select(FactBinding).where(
            FactBinding.company_id == company_id,
            FactBinding.artifact_type == artifact_type,
            FactBinding.artifact_id.in_(artifact_ids),
        )
    )
    grouped: dict[UUID, list[FactBinding]] = {}
    for item in rows:
        grouped.setdefault(item.artifact_id, []).append(item)
    return grouped


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
    # All approval transactions acquire this lock before their target/dependency
    # locks. The parent lock also prevents a new tenant-scoped input appearing
    # between feasibility/evidence revalidation and commit via its foreign key.
    await session.scalar(select(Company.id).where(Company.id == company_id).with_for_update())
    return await session.scalar(
        select(Approval)
        .where(
            Approval.company_id == company_id,
            Approval.id == approval_id,
        )
        .with_for_update()
    )


async def lock_review_dependencies(session: AsyncSession, *, approval: Approval) -> None:
    """Hold mutable domain inputs stable through the short decision transaction.

    Rows are acquired in physical table / UUID order for every decision. The
    company parent lock above closes insert phantoms; these locks close updates
    and deletes. This deliberately favors P0 correctness over tenant throughput.
    """
    from app.db.models.assurance import (
        ClaimCitation,
        DisclosureClaim,
        DisclosureRequirement,
        EvidenceGap,
        Standard,
    )
    from app.db.models.core import DataSource, ReportingPeriod, Site, SourceDocument
    from app.db.models.dispatch import (
        DispatchScenario,
        FlexibleLoad,
        GridForecast,
        OperatingConstraint,
    )
    from app.db.models.ledger import FactBinding, LedgerEventEvidence
    from app.db.models.semantic import PolicyDefinition

    shared = {Actor, EvidenceItem, SourceDocument, DataSource, FactBinding, Site, MethodDefinition}
    domain = {
        "procurement_recommendation": {
            ProcurementScenario,
            Supplier,
            SupplierProduct,
            SupplierScore,
            CarbonMeasurement,
        },
        "disclosure_draft": {
            Standard,
            DisclosureRequirement,
            DisclosureClaim,
            ClaimCitation,
            EvidenceGap,
            CalculationRun,
            CarbonMeasurement,
            ReportingPeriod,
        },
        "dispatch_recommendation": {
            DispatchScenario,
            FlexibleLoad,
            GridForecast,
            OperatingConstraint,
            PolicyDefinition,
            LedgerEventEvidence,
        },
    }.get(approval.target_type, set())
    for model in sorted(shared | domain, key=lambda item: item.__table__.fullname):
        primary_key = tuple(model.__table__.primary_key.columns)
        await session.execute(
            select(*primary_key)
            .where(model.company_id == approval.company_id)
            .order_by(*primary_key)
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
    for_update: bool = False,
) -> Actor | None:
    query = select(Actor).where(Actor.company_id == company_id, Actor.id == actor_id)
    if for_update:
        query = query.with_for_update()
    return await session.scalar(query)


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


async def get_pending_approval_for_target(
    session: AsyncSession,
    *,
    company_id: UUID,
    target_type: str,
    target_id: UUID,
) -> Approval | None:
    """Load the one pending generic preview for a tenant-scoped target."""
    return await session.scalar(
        select(Approval).where(
            Approval.company_id == company_id,
            Approval.target_type == target_type,
            Approval.target_id == target_id,
            Approval.status == "pending",
        )
    )


async def get_approval_by_idempotency_key(
    session: AsyncSession,
    *,
    company_id: UUID,
    idempotency_key: str,
) -> Approval | None:
    """Load an approval command result by its tenant-scoped idempotency key."""
    return await session.scalar(
        select(Approval).where(
            Approval.company_id == company_id,
            Approval.idempotency_key == idempotency_key,
        )
    )
