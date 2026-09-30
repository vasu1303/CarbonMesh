from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.procurement.schemas import MaterialConstraints
from app.modules.procurement.scoring import (
    ProductFacts,
    ScenarioFacts,
    calculate_assessment,
)

EVIDENCE_ID = UUID("00000000-0000-0000-0000-000000000103")


def current_product() -> ProductFacts:
    return ProductFacts(
        material_code="PACKAGING_TRAY",
        category="packaging",
        pcf_kgco2e_per_unit=Decimal("2.8"),
        pcf_unit="kgCO2e/kg",
        circularity_score=Decimal(55),
        evidence_quality_score=Decimal(80),
        lead_time_days=10,
        unit_cost=Decimal("1.000000"),
        currency="USD",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        evidence_item_id=EVIDENCE_ID,
        supplier_risk="low",
    )


def alternative_product() -> ProductFacts:
    return ProductFacts(
        material_code="PACKAGING_TRAY",
        category="packaging",
        pcf_kgco2e_per_unit=Decimal("1.9"),
        pcf_unit="kgCO2e/kg",
        circularity_score=Decimal(80),
        evidence_quality_score=Decimal(90),
        lead_time_days=12,
        unit_cost=Decimal("1.032000"),
        currency="USD",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        evidence_item_id=EVIDENCE_ID,
        supplier_risk="low",
    )


def scenario() -> ScenarioFacts:
    return ScenarioFacts(
        quantity=Decimal(12000),
        current_unit_cost=Decimal("1.000000"),
        currency="USD",
        max_cost_increase_pct=Decimal(5),
        max_lead_time_days=20,
        minimum_circularity_score=Decimal(50),
        material_constraints=MaterialConstraints(),
        period_start=date(2026, 7, 1),
        period_end=date(2026, 9, 30),
        carbon_weight=Decimal("0.4000"),
        evidence_weight=Decimal("0.2500"),
        circularity_weight=Decimal("0.2000"),
        operational_fit_weight=Decimal("0.1500"),
    )


def test_expected_demo_impact_is_calculated_with_decimal() -> None:
    result = calculate_assessment(
        current=current_product(), candidate=alternative_product(), scenario=scenario()
    )

    assert result.feasible is True
    assert result.infeasibility_reasons == []
    assert result.projected_footprint_kgco2e == Decimal("22800.000000")
    assert result.avoided_kgco2e == Decimal("10800.000000")
    assert result.reduction_pct == Decimal("32.1429")
    assert result.cost_delta_pct == Decimal("3.2000")
    assert result.lead_time_delta_days == 2
    assert result.carbon_score == Decimal("32.1429")
    assert result.total_score == Decimal("57.3572")


def test_cost_is_a_hard_constraint_and_does_not_change_weighted_score() -> None:
    baseline = calculate_assessment(
        current=current_product(), candidate=alternative_product(), scenario=scenario()
    )
    more_expensive = replace(alternative_product(), unit_cost=Decimal("1.049000"))
    within_ceiling = calculate_assessment(
        current=current_product(), candidate=more_expensive, scenario=scenario()
    )
    tight_constraint = replace(scenario(), max_cost_increase_pct=Decimal(3))
    rejected = calculate_assessment(
        current=current_product(), candidate=alternative_product(), scenario=tight_constraint
    )

    assert within_ceiling.feasible is True
    assert within_ceiling.total_score == baseline.total_score
    assert rejected.feasible is False
    assert rejected.total_score == baseline.total_score
    assert {reason["code"] for reason in rejected.infeasibility_reasons} == {
        "cost_ceiling_exceeded"
    }
    assert tight_constraint.max_cost_increase_pct == Decimal(3)


def test_all_hard_constraint_failures_are_explicit() -> None:
    candidate = replace(
        alternative_product(),
        material_code="INCOMPATIBLE",
        category="packaging",
        pcf_kgco2e_per_unit=Decimal("3.0"),
        pcf_unit="kgCO2e/item",
        circularity_score=Decimal(10),
        evidence_item_id=None,
        lead_time_days=50,
        currency="EUR",
        effective_from=date(2027, 1, 1),
        supplier_risk="high",
    )
    constrained = replace(
        scenario(),
        material_constraints=MaterialConstraints(excluded_risk_levels=["HIGH"]),
    )

    result = calculate_assessment(
        current=current_product(), candidate=candidate, scenario=constrained
    )

    assert result.feasible is False
    assert {reason["code"] for reason in result.infeasibility_reasons} == {
        "material_incompatible",
        "currency_mismatch",
        "lead_time_exceeded",
        "circularity_below_minimum",
        "missing_evidence",
        "outside_effective_period",
        "pcf_unit_mismatch",
        "no_carbon_reduction",
        "supplier_risk_excluded",
    }


def test_zero_lead_time_ceiling_has_defined_operational_score() -> None:
    no_wait = replace(alternative_product(), lead_time_days=0)
    zero_ceiling = replace(scenario(), max_lead_time_days=0)
    result = calculate_assessment(
        current=current_product(), candidate=no_wait, scenario=zero_ceiling
    )

    assert result.operational_fit_score == Decimal("100.0000")
    assert result.feasible is True


def test_material_constraints_reject_unknown_or_duplicate_values() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MaterialConstraints.model_validate({"minimum_recycled_content": "50"})

    with pytest.raises(ValidationError, match="must not contain duplicates"):
        MaterialConstraints(excluded_risk_levels=["HIGH", "high"])


def test_allowed_material_codes_are_enforced_as_a_hard_constraint() -> None:
    constrained = replace(
        scenario(),
        material_constraints=MaterialConstraints(allowed_material_codes=["OTHER"]),
    )

    result = calculate_assessment(
        current=current_product(), candidate=alternative_product(), scenario=constrained
    )

    assert result.feasible is False
    assert "material_incompatible" in {reason["code"] for reason in result.infeasibility_reasons}
