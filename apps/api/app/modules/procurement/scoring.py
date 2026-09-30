from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from app.modules.procurement.schemas import InfeasibilityReason, MaterialConstraints

ZERO = Decimal(0)
ONE_HUNDRED = Decimal(100)
SCORE_QUANTUM = Decimal("0.0001")
CARBON_QUANTUM = Decimal("0.000001")
PERCENT_QUANTUM = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class ProductFacts:
    material_code: str
    category: str
    pcf_kgco2e_per_unit: Decimal
    pcf_unit: str
    circularity_score: Decimal
    evidence_quality_score: Decimal
    lead_time_days: int
    unit_cost: Decimal
    currency: str
    effective_from: date
    effective_to: date | None
    evidence_item_id: UUID | None
    supplier_risk: str


@dataclass(frozen=True, slots=True)
class ScenarioFacts:
    quantity: Decimal
    current_unit_cost: Decimal
    currency: str
    max_cost_increase_pct: Decimal
    max_lead_time_days: int
    minimum_circularity_score: Decimal
    material_constraints: MaterialConstraints
    period_start: date
    period_end: date
    carbon_weight: Decimal
    evidence_weight: Decimal
    circularity_weight: Decimal
    operational_fit_weight: Decimal


@dataclass(frozen=True, slots=True)
class CalculatedAssessment:
    carbon_score: Decimal
    evidence_score: Decimal
    circularity_score: Decimal
    operational_fit_score: Decimal
    total_score: Decimal
    feasible: bool
    infeasibility_reasons: list[dict[str, Any]]
    projected_footprint_kgco2e: Decimal
    avoided_kgco2e: Decimal
    reduction_pct: Decimal
    cost_delta_pct: Decimal
    lead_time_delta_days: int


def quantize(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def clamp_score(value: Decimal) -> Decimal:
    return quantize(min(ONE_HUNDRED, max(ZERO, value)), SCORE_QUANTUM)


def infer_supplier_risk(metadata: dict[str, Any] | None) -> str:
    raw = (metadata or {}).get("risk_level", (metadata or {}).get("risk", "unknown"))
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return "unknown"


def calculate_assessment(
    *,
    current: ProductFacts,
    candidate: ProductFacts,
    scenario: ScenarioFacts,
) -> CalculatedAssessment:
    """Calculate scores, feasibility, and impact without I/O or floating point."""
    reasons: list[InfeasibilityReason] = []

    allowed_materials = scenario.material_constraints.allowed_material_codes
    if allowed_materials:
        material_compatible = candidate.material_code in set(allowed_materials)
        material_requirement = ",".join(str(value) for value in allowed_materials)
    else:
        material_compatible = candidate.material_code == current.material_code
        material_requirement = current.material_code
    if not material_compatible or candidate.category != current.category:
        reasons.append(
            InfeasibilityReason(
                code="material_incompatible",
                message="The product does not satisfy the frozen material compatibility constraint.",
                actual=f"{candidate.material_code}/{candidate.category}",
                required=f"{material_requirement}/{current.category}",
            )
        )

    if candidate.currency != scenario.currency:
        reasons.append(
            InfeasibilityReason(
                code="currency_mismatch",
                message="A deterministic cost comparison requires the frozen scenario currency.",
                actual=candidate.currency,
                required=scenario.currency,
            )
        )

    cost_delta_pct = quantize(
        ((candidate.unit_cost - scenario.current_unit_cost) / scenario.current_unit_cost)
        * ONE_HUNDRED,
        PERCENT_QUANTUM,
    )
    if cost_delta_pct > scenario.max_cost_increase_pct:
        reasons.append(
            InfeasibilityReason(
                code="cost_ceiling_exceeded",
                message="The product exceeds the frozen maximum unit-cost increase.",
                actual=str(cost_delta_pct),
                required=str(scenario.max_cost_increase_pct),
            )
        )

    if candidate.lead_time_days > scenario.max_lead_time_days:
        reasons.append(
            InfeasibilityReason(
                code="lead_time_exceeded",
                message="The product exceeds the frozen lead-time ceiling.",
                actual=candidate.lead_time_days,
                required=scenario.max_lead_time_days,
            )
        )

    if candidate.circularity_score < scenario.minimum_circularity_score:
        reasons.append(
            InfeasibilityReason(
                code="circularity_below_minimum",
                message="The product is below the frozen minimum circularity score.",
                actual=str(candidate.circularity_score),
                required=str(scenario.minimum_circularity_score),
            )
        )

    if candidate.evidence_item_id is None:
        reasons.append(
            InfeasibilityReason(
                code="missing_evidence",
                message="The product has no evidence item supporting its carbon footprint.",
                actual=False,
                required=True,
            )
        )

    if candidate.effective_from > scenario.period_end or (
        candidate.effective_to is not None and candidate.effective_to < scenario.period_start
    ):
        reasons.append(
            InfeasibilityReason(
                code="outside_effective_period",
                message="The product evidence is not effective for the reporting period.",
                actual=f"{candidate.effective_from}/{candidate.effective_to or 'open'}",
                required=f"{scenario.period_start}/{scenario.period_end}",
            )
        )

    if candidate.pcf_unit != current.pcf_unit:
        reasons.append(
            InfeasibilityReason(
                code="pcf_unit_mismatch",
                message="Product carbon footprints must use the same unit for deterministic comparison.",
                actual=candidate.pcf_unit,
                required=current.pcf_unit,
            )
        )

    if candidate.pcf_kgco2e_per_unit >= current.pcf_kgco2e_per_unit:
        reasons.append(
            InfeasibilityReason(
                code="no_carbon_reduction",
                message="The alternative does not reduce product carbon footprint.",
                actual=str(candidate.pcf_kgco2e_per_unit),
                required=f"less than {current.pcf_kgco2e_per_unit}",
            )
        )

    excluded_risks = scenario.material_constraints.excluded_risk_levels
    if candidate.supplier_risk in set(excluded_risks):
        reasons.append(
            InfeasibilityReason(
                code="supplier_risk_excluded",
                message="The supplier risk level is excluded by the frozen scenario.",
                actual=candidate.supplier_risk,
                required="not excluded",
            )
        )

    if current.pcf_kgco2e_per_unit > ZERO:
        carbon_score = clamp_score(
            (
                (current.pcf_kgco2e_per_unit - candidate.pcf_kgco2e_per_unit)
                / current.pcf_kgco2e_per_unit
            )
            * ONE_HUNDRED
        )
    else:
        carbon_score = ZERO.quantize(SCORE_QUANTUM)
    evidence_score = clamp_score(candidate.evidence_quality_score)
    circularity_score = clamp_score(candidate.circularity_score)
    if scenario.max_lead_time_days == 0:
        operational_fit_score = ONE_HUNDRED if candidate.lead_time_days == 0 else ZERO
    else:
        operational_fit_score = clamp_score(
            ONE_HUNDRED
            - (Decimal(candidate.lead_time_days) / Decimal(scenario.max_lead_time_days))
            * ONE_HUNDRED
        )
    operational_fit_score = quantize(operational_fit_score, SCORE_QUANTUM)

    total_score = clamp_score(
        scenario.carbon_weight * carbon_score
        + scenario.evidence_weight * evidence_score
        + scenario.circularity_weight * circularity_score
        + scenario.operational_fit_weight * operational_fit_score
    )

    baseline = quantize(
        scenario.quantity * current.pcf_kgco2e_per_unit,
        CARBON_QUANTUM,
    )
    projected = quantize(
        scenario.quantity * candidate.pcf_kgco2e_per_unit,
        CARBON_QUANTUM,
    )
    avoided = quantize(max(ZERO, baseline - projected), CARBON_QUANTUM)
    reduction_pct = (
        quantize((avoided / baseline) * ONE_HUNDRED, PERCENT_QUANTUM)
        if baseline > ZERO
        else ZERO.quantize(PERCENT_QUANTUM)
    )

    return CalculatedAssessment(
        carbon_score=carbon_score,
        evidence_score=evidence_score,
        circularity_score=circularity_score,
        operational_fit_score=operational_fit_score,
        total_score=total_score,
        feasible=not reasons,
        infeasibility_reasons=[reason.model_dump(mode="json") for reason in reasons],
        projected_footprint_kgco2e=projected,
        avoided_kgco2e=avoided,
        reduction_pct=reduction_pct,
        cost_delta_pct=cost_delta_pct,
        lead_time_delta_days=candidate.lead_time_days - current.lead_time_days,
    )
