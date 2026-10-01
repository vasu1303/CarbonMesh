from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import CarbonMeasurement
from app.db.models.procurement import ProcurementScenario, Recommendation, SupplierScore
from app.db.models.semantic import MethodDefinition
from app.modules.ledger.service import normalize_json, payload_sha256
from app.modules.procurement.repository import ProcurementRepository, ProductRecord

REVIEW_SNAPSHOT_VERSION = "procurement-review.initial"
RECOMMENDATION_PAYLOAD_VERSION = "procurement-recommendation.v2"
SCENARIO_DECIMAL_FIELDS = (
    (("quantity",), Decimal("0.000001")),
    (("current_product", "unit_cost"), Decimal("0.000001")),
    (("constraints", "max_cost_increase_pct"), Decimal("0.0001")),
    (("constraints", "minimum_circularity_score"), Decimal("0.0001")),
)


@dataclass(frozen=True, slots=True)
class RecommendationReviewInputs:
    """Current persisted inputs needed to verify one frozen recommendation preview."""

    scenario: ProcurementScenario
    baseline: ProductRecord
    selected: ProductRecord
    score: SupplierScore
    method: MethodDefinition
    measurement: CarbonMeasurement
    candidate_assessments: list[tuple[SupplierScore, ProductRecord]]
    eligible_candidates: list[ProductRecord]


def build_review_source_state(
    *,
    scenario: ProcurementScenario,
    baseline: ProductRecord,
    selected: ProductRecord,
    score: SupplierScore,
    method: MethodDefinition,
    measurement: CarbonMeasurement,
    candidate_assessments: list[tuple[SupplierScore, ProductRecord]],
    eligible_candidates: list[ProductRecord],
) -> dict[str, Any]:
    """Capture mutable inputs whose change must invalidate an approval preview."""

    def product_state(record: ProductRecord) -> dict[str, Any]:
        product = record.product
        supplier = record.supplier
        evidence = record.evidence
        return {
            "product": {
                "id": product.id,
                "supplier_id": product.supplier_id,
                "evidence_item_id": product.evidence_item_id,
                "product_code": product.product_code,
                "name": product.name,
                "material_code": product.material_code,
                "category": product.category,
                "description": product.description,
                "pcf_kgco2e_per_unit": product.pcf_kgco2e_per_unit,
                "pcf_unit": product.pcf_unit,
                "circularity_score": product.circularity_score,
                "recycled_content_pct": product.recycled_content_pct,
                "recyclable_pct": product.recyclable_pct,
                "evidence_quality_score": product.evidence_quality_score,
                "lead_time_days": product.lead_time_days,
                "unit_cost": product.unit_cost,
                "currency": product.currency,
                "effective_from": product.effective_from,
                "effective_to": product.effective_to,
                "is_active": product.is_active,
            },
            "supplier": {
                "id": supplier.id,
                "supplier_code": supplier.supplier_code,
                "name": supplier.name,
                "country_code": supplier.country_code,
                "status": supplier.status,
                "metadata": supplier.supplier_metadata,
            },
            "evidence": (
                {
                    "id": evidence.id,
                    "source_document_id": evidence.source_document_id,
                    "evidence_type": evidence.evidence_type,
                    "locator": evidence.locator,
                    "content_text": evidence.content_text,
                    "checksum": evidence.checksum,
                    "metadata": evidence.evidence_metadata,
                    "embedding_model": evidence.embedding_model,
                    "embedded_at": evidence.embedded_at,
                }
                if evidence is not None
                else None
            ),
        }

    def score_state(candidate_score: SupplierScore) -> dict[str, Any]:
        return {
            "id": candidate_score.id,
            "supplier_product_id": candidate_score.supplier_product_id,
            "method_definition_id": candidate_score.method_definition_id,
            "carbon_score": candidate_score.carbon_score,
            "evidence_score": candidate_score.evidence_score,
            "circularity_score": candidate_score.circularity_score,
            "operational_fit_score": candidate_score.operational_fit_score,
            "total_score": candidate_score.total_score,
            "rank": candidate_score.rank,
            "feasible": candidate_score.feasible,
            "infeasibility_reasons": candidate_score.infeasibility_reasons,
        }

    sorted_assessments = sorted(
        candidate_assessments,
        key=lambda item: str(item[1].product.id),
    )
    sorted_eligible_candidates = sorted(
        eligible_candidates,
        key=lambda item: str(item.product.id),
    )
    return normalize_json(
        {
            "scenario": {
                "id": scenario.id,
                "site_id": scenario.site_id,
                "reporting_period_id": scenario.reporting_period_id,
                "current_product_id": scenario.current_product_id,
                "carbon_measurement_id": scenario.carbon_measurement_id,
                "method_definition_id": scenario.method_definition_id,
                "quantity": scenario.quantity,
                "quantity_unit": scenario.quantity_unit,
                "current_unit_cost": scenario.current_unit_cost,
                "currency": scenario.currency,
                "max_cost_increase_pct": scenario.max_cost_increase_pct,
                "max_lead_time_days": scenario.max_lead_time_days,
                "minimum_circularity_score": scenario.minimum_circularity_score,
                "material_constraints": scenario.material_constraints,
                "carbon_weight": scenario.carbon_weight,
                "evidence_weight": scenario.evidence_weight,
                "circularity_weight": scenario.circularity_weight,
                "operational_fit_weight": scenario.operational_fit_weight,
                "analysis_signature": scenario.analysis_signature,
            },
            "baseline": product_state(baseline),
            "selected": product_state(selected),
            "score": score_state(score),
            "candidate_assessments": [
                {
                    "product_state": product_state(candidate),
                    "score": score_state(candidate_score),
                }
                for candidate_score, candidate in sorted_assessments
            ],
            "eligible_candidates": [
                product_state(candidate) for candidate in sorted_eligible_candidates
            ],
            "measurement": {
                "id": measurement.id,
                "calculation_run_id": measurement.calculation_run_id,
                "site_id": measurement.site_id,
                "reporting_period_id": measurement.reporting_period_id,
                "metric_definition_id": measurement.metric_definition_id,
                "ledger_event_id": measurement.ledger_event_id,
                "value_kgco2e": measurement.value_kgco2e,
                "unit": measurement.unit,
                "confidence": measurement.confidence,
                "status": measurement.status,
                "formula": measurement.formula,
                "output_hash": measurement.output_hash,
                "verified_at": measurement.verified_at,
            },
            "method": {
                "id": method.id,
                "method_type": method.method_type,
                "key": method.key,
                "version": method.version,
                "name": method.name,
                "code_version": method.code_version,
                "configuration": method.configuration,
                "effective_from": method.effective_from,
                "effective_to": method.effective_to,
                "is_active": method.is_active,
            },
        }
    )


def review_source_hash(source_state: dict[str, Any]) -> str:
    return payload_sha256(source_state)[1]


def canonicalize_scenario_context(frozen_context: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize scenario inputs to the precision persisted by PostgreSQL."""
    normalized: dict[str, Any] = normalize_json(frozen_context)
    for path, quantum in SCENARIO_DECIMAL_FIELDS:
        container = normalized
        for key in path[:-1]:
            nested = container.get(key)
            if not isinstance(nested, dict):
                break
            container = nested
        else:
            field = path[-1]
            value = container.get(field)
            try:
                canonical = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
            except (InvalidOperation, TypeError, ValueError):
                continue
            container[field] = format(canonical, "f")
    return normalized


def scenario_context_signature(frozen_context: dict[str, Any]) -> str:
    """Hash semantic inputs, excluding lineage metadata and persisted results."""
    signature_context = canonicalize_scenario_context(frozen_context)
    signature_context.pop("agent_run_id", None)
    signature_context.pop("assessment_snapshot", None)
    return payload_sha256(signature_context)[1]


def build_recommendation_payload(
    *,
    company_id: UUID,
    scenario_id: UUID,
    baseline_product_id: UUID,
    recommended_product_id: UUID,
    supplier_score_id: UUID,
    analysis_signature: str,
    projected_footprint_kgco2e: Any,
    avoided_kgco2e: Any,
    reduction_pct: Any,
    cost_delta_pct: Any,
    lead_time_delta_days: int,
    rationale_template: str,
    impact_snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": RECOMMENDATION_PAYLOAD_VERSION,
        "company_id": company_id,
        "scenario_id": scenario_id,
        "baseline_product_id": baseline_product_id,
        "recommended_product_id": recommended_product_id,
        "supplier_score_id": supplier_score_id,
        "analysis_signature": analysis_signature,
        "projected_footprint_kgco2e": projected_footprint_kgco2e,
        "avoided_kgco2e": avoided_kgco2e,
        "reduction_pct": reduction_pct,
        "cost_delta_pct": cost_delta_pct,
        "lead_time_delta_days": lead_time_delta_days,
        "rationale_template": rationale_template,
        "impact": impact_snapshot,
    }


def stored_recommendation_payload(recommendation: Recommendation) -> dict[str, Any]:
    """Rebuild the exact immutable payload represented by a stored recommendation."""
    return build_recommendation_payload(
        company_id=recommendation.company_id,
        scenario_id=recommendation.scenario_id,
        baseline_product_id=recommendation.baseline_product_id,
        recommended_product_id=recommendation.recommended_product_id,
        supplier_score_id=recommendation.supplier_score_id,
        analysis_signature=recommendation.analysis_signature,
        projected_footprint_kgco2e=recommendation.projected_footprint_kgco2e,
        avoided_kgco2e=recommendation.avoided_kgco2e,
        reduction_pct=recommendation.reduction_pct,
        cost_delta_pct=recommendation.cost_delta_pct,
        lead_time_delta_days=recommendation.lead_time_delta_days,
        rationale_template=recommendation.rationale_template,
        impact_snapshot=recommendation.impact_snapshot,
    )


def recommendation_preview_matches_inputs(
    recommendation: Recommendation,
    inputs: RecommendationReviewInputs | None,
) -> bool:
    """Compare one preview with already-loaded current inputs without database access."""
    impact = recommendation.impact_snapshot
    expected_source_hash = impact.get("source_state_hash")
    review = impact.get("review")
    if (
        inputs is None
        or not isinstance(expected_source_hash, str)
        or not isinstance(review, dict)
        or not inputs.candidate_assessments
        or inputs.scenario.analysis_signature != recommendation.analysis_signature
    ):
        return False

    if (
        scenario_context_signature(inputs.scenario.frozen_context)
        != inputs.scenario.analysis_signature
    ):
        return False
    current_source_hash = review_source_hash(
        build_review_source_state(
            scenario=inputs.scenario,
            baseline=inputs.baseline,
            selected=inputs.selected,
            score=inputs.score,
            method=inputs.method,
            measurement=inputs.measurement,
            candidate_assessments=inputs.candidate_assessments,
            eligible_candidates=inputs.eligible_candidates,
        )
    )
    if current_source_hash != expected_source_hash:
        return False

    return payload_sha256(stored_recommendation_payload(recommendation))[1] == (
        recommendation.payload_hash
    )


def recommendation_previews_are_current(
    recommendations: Collection[Recommendation],
    inputs_by_recommendation_id: Mapping[UUID, RecommendationReviewInputs],
) -> dict[UUID, bool]:
    """Evaluate a bounded recommendation page from one bulk-loaded input snapshot."""
    return {
        recommendation.id: recommendation_preview_matches_inputs(
            recommendation,
            inputs_by_recommendation_id.get(recommendation.id),
        )
        for recommendation in recommendations
    }


async def recommendation_preview_is_current(
    session: AsyncSession, recommendation: Recommendation
) -> bool:
    """Verify the preview hash and all mutable reviewed inputs against their snapshot."""
    impact = recommendation.impact_snapshot
    expected_source_hash = impact.get("source_state_hash")
    review = impact.get("review")
    if not isinstance(expected_source_hash, str) or not isinstance(review, dict):
        return False

    repository = ProcurementRepository(session)
    scenario = await repository.get_scenario(
        company_id=recommendation.company_id,
        scenario_id=recommendation.scenario_id,
    )
    score = await repository.get_score(
        company_id=recommendation.company_id,
        score_id=recommendation.supplier_score_id,
    )
    baseline = await repository.get_product(
        company_id=recommendation.company_id,
        product_id=recommendation.baseline_product_id,
    )
    selected = await repository.get_product(
        company_id=recommendation.company_id,
        product_id=recommendation.recommended_product_id,
    )
    if scenario is None or score is None or baseline is None or selected is None:
        return False
    measurement = await repository.get_measurement(
        company_id=recommendation.company_id,
        measurement_id=scenario.carbon_measurement_id,
    )
    candidate_assessments = await repository.list_scores(
        company_id=recommendation.company_id,
        scenario_id=scenario.id,
    )
    eligible_candidates = await repository.list_candidate_products(
        company_id=recommendation.company_id,
        current_product_id=scenario.current_product_id,
        category=baseline.product.category,
    )
    method = await repository.get_method(
        company_id=recommendation.company_id,
        method_id=scenario.method_definition_id,
    )
    if (
        method is None
        or measurement is None
        or not candidate_assessments
        or scenario.analysis_signature != recommendation.analysis_signature
    ):
        return False

    if scenario_context_signature(scenario.frozen_context) != scenario.analysis_signature:
        return False
    return recommendation_preview_matches_inputs(
        recommendation,
        RecommendationReviewInputs(
            scenario=scenario,
            baseline=baseline,
            selected=selected,
            score=score,
            method=method,
            measurement=measurement,
            candidate_assessments=candidate_assessments,
            eligible_candidates=eligible_candidates,
        ),
    )
