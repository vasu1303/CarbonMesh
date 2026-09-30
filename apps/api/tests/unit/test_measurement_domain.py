from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.measurement.domain import (
    AmbiguousFactorError,
    FactorCandidate,
    InvalidQuantityError,
    NoMatchingFactorError,
    calculate_confidence,
    calculate_emissions,
    calculate_variance,
    normalize_mass_to_kg,
    resolve_factor,
)


def _factor(
    *,
    product_code: str | None = None,
    material_code: str | None = "PACKAGING_TRAY",
    geography: str = "GLOBAL",
    denominator_unit: str = "kg",
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = None,
) -> FactorCandidate:
    return FactorCandidate(
        id=uuid4(),
        factor_code=f"factor-{uuid4().hex}",
        version="1.0.0",
        material_code=material_code,
        product_code=product_code,
        geography=geography,
        factor_value=Decimal("2.8"),
        numerator_unit="kgCO2e",
        denominator_unit=denominator_unit,
        effective_from=effective_from,
        effective_to=effective_to,
        source_quality=Decimal("0.90"),
        factor_specificity=Decimal("0.95"),
        factor_recency=Decimal("0.85"),
        evidence_item_id=uuid4(),
    )


@pytest.mark.parametrize(
    ("quantity", "unit", "expected"),
    [
        (Decimal(12000), "kg", Decimal("12000.000000")),
        (Decimal(12), "tonnes", Decimal("12000.000000")),
        (Decimal(1000), "g", Decimal("1.000000")),
        (Decimal(10), "lb", Decimal("4.535924")),
    ],
)
def test_normalize_supported_mass_units_to_kg(
    quantity: Decimal,
    unit: str,
    expected: Decimal,
) -> None:
    assert normalize_mass_to_kg(quantity, unit) == expected


def test_demo_measurement_confidence_and_variance_are_decimal_deterministic() -> None:
    emissions = calculate_emissions(Decimal(12000), Decimal("2.8"))
    confidence = calculate_confidence(
        source_quality=Decimal("0.90"),
        factor_specificity=Decimal("0.80"),
        factor_recency=Decimal("0.70"),
        record_completeness=Decimal("1.00"),
    )
    variance = calculate_variance(emissions, Decimal(30000))

    assert emissions == Decimal("33600.000000")
    assert confidence.overall == Decimal("0.84000")
    assert variance.variance_kgco2e == Decimal("3600.000000")
    assert variance.variance_pct == Decimal("12.0000")


def test_measurement_numeric_values_must_fit_persisted_precision() -> None:
    with pytest.raises(InvalidQuantityError, match=r"Numeric\(24,6\)"):
        normalize_mass_to_kg(Decimal("999999999999999999.999999"), "tonnes")

    with pytest.raises(InvalidQuantityError, match=r"Numeric\(24,6\)"):
        calculate_emissions(
            Decimal("999999999999999999.999999"),
            Decimal(2),
        )


def test_factor_resolution_prefers_product_then_geography_then_unit() -> None:
    generic = _factor()
    product_global = _factor(product_code="TRAY-CURRENT")
    product_india_grams = _factor(
        product_code="TRAY-CURRENT",
        geography="IN",
        denominator_unit="g",
    )
    product_india_kg = _factor(product_code="TRAY-CURRENT", geography="IN")

    selected = resolve_factor(
        [generic, product_global, product_india_grams, product_india_kg],
        material_code="PACKAGING_TRAY",
        product_code="TRAY-CURRENT",
        geography="IN",
        effective_on=date(2026, 9, 30),
    )

    assert selected.candidate.id == product_india_kg.id
    assert selected.factor_kgco2e_per_kg == Decimal("2.8")


def test_factor_resolution_rejects_equal_specificity_ambiguity() -> None:
    first = _factor(product_code="TRAY-CURRENT", geography="IN")
    second = _factor(product_code="TRAY-CURRENT", geography="IN")

    with pytest.raises(AmbiguousFactorError) as caught:
        resolve_factor(
            [first, second],
            material_code="PACKAGING_TRAY",
            product_code="TRAY-CURRENT",
            geography="IN",
            effective_on=date(2026, 9, 30),
        )

    assert set(caught.value.factor_ids) == {first.id, second.id}


def test_factor_resolution_returns_unsupported_when_period_does_not_match() -> None:
    expired = _factor(effective_to=date(2026, 6, 30))

    with pytest.raises(NoMatchingFactorError):
        resolve_factor(
            [expired],
            material_code="PACKAGING_TRAY",
            product_code=None,
            geography="IN",
            effective_on=date(2026, 9, 30),
        )
