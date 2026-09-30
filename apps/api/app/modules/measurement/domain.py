from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any
from uuid import UUID

KG_QUANTUM = Decimal("0.000001")
EMISSIONS_QUANTUM = Decimal("0.000001")
FACTOR_QUANTUM = Decimal("0.000000000001")
CONFIDENCE_QUANTUM = Decimal("0.00001")
PERCENT_QUANTUM = Decimal("0.0001")
NUMERIC_24_6_LIMIT = Decimal(1000000000000000000)
NUMERIC_24_12_LIMIT = Decimal(1000000000000)

DEFAULT_CONFIDENCE_WEIGHTS: dict[str, Decimal] = {
    "source_quality": Decimal("0.40"),
    "factor_specificity": Decimal("0.30"),
    "factor_recency": Decimal("0.20"),
    "record_completeness": Decimal("0.10"),
}

_KG_PER_UNIT: dict[str, Decimal] = {
    "kg": Decimal(1),
    "kilogram": Decimal(1),
    "kilograms": Decimal(1),
    "g": Decimal("0.001"),
    "gram": Decimal("0.001"),
    "grams": Decimal("0.001"),
    "mg": Decimal("0.000001"),
    "milligram": Decimal("0.000001"),
    "milligrams": Decimal("0.000001"),
    "t": Decimal(1000),
    "tonne": Decimal(1000),
    "tonnes": Decimal(1000),
    "metric ton": Decimal(1000),
    "metric tons": Decimal(1000),
    "metric tonne": Decimal(1000),
    "metric tonnes": Decimal(1000),
    "lb": Decimal("0.45359237"),
    "lbs": Decimal("0.45359237"),
    "pound": Decimal("0.45359237"),
    "pounds": Decimal("0.45359237"),
}


class MeasurementDomainError(ValueError):
    """Base class for deterministic measurement validation failures."""


class UnsupportedMassUnitError(MeasurementDomainError):
    def __init__(self, unit: str) -> None:
        super().__init__(f"Mass unit '{unit}' is not supported.")
        self.unit = unit


class InvalidQuantityError(MeasurementDomainError):
    pass


class NoMatchingFactorError(MeasurementDomainError):
    pass


class AmbiguousFactorError(MeasurementDomainError):
    def __init__(self, factor_ids: Iterable[UUID]) -> None:
        self.factor_ids = tuple(factor_ids)
        super().__init__("More than one equally specific emission factor is valid.")


class InvalidConfidenceConfigurationError(MeasurementDomainError):
    pass


@dataclass(frozen=True, slots=True)
class FactorCandidate:
    id: UUID
    factor_code: str
    version: str
    material_code: str | None
    product_code: str | None
    geography: str
    factor_value: Decimal
    numerator_unit: str
    denominator_unit: str
    effective_from: date
    effective_to: date | None
    source_quality: Decimal
    factor_specificity: Decimal
    factor_recency: Decimal
    evidence_item_id: UUID


@dataclass(frozen=True, slots=True)
class ResolvedFactor:
    candidate: FactorCandidate
    factor_kgco2e_per_kg: Decimal
    specificity_rank: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class ConfidenceBreakdown:
    source_quality: Decimal
    factor_specificity: Decimal
    factor_recency: Decimal
    record_completeness: Decimal
    overall: Decimal
    weights: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class VarianceResult:
    variance_kgco2e: Decimal
    variance_pct: Decimal | None


def normalize_unit_token(unit: str) -> str:
    return " ".join(unit.strip().lower().replace("_", " ").replace("-", " ").split())


def kilograms_per_unit(unit: str) -> Decimal:
    try:
        return _KG_PER_UNIT[normalize_unit_token(unit)]
    except KeyError:
        raise UnsupportedMassUnitError(unit) from None


def normalize_mass_to_kg(
    quantity: Decimal,
    unit: str,
    *,
    rounding: str = ROUND_HALF_EVEN,
) -> Decimal:
    if not quantity.is_finite() or quantity < 0:
        raise InvalidQuantityError("Mass quantity must be finite and non-negative.")
    try:
        with localcontext() as context:
            context.prec = 60
            normalized = quantity * kilograms_per_unit(unit)
            quantized = normalized.quantize(KG_QUANTUM, rounding=rounding)
    except InvalidOperation as error:
        raise InvalidQuantityError("Mass quantity could not be normalized.") from error
    if not quantized.is_finite() or abs(quantized) >= NUMERIC_24_6_LIMIT:
        raise InvalidQuantityError("Normalized mass exceeds the supported Numeric(24,6) range.")
    return quantized


def normalize_factor_to_per_kg(
    factor_value: Decimal,
    denominator_unit: str,
    *,
    rounding: str = ROUND_HALF_EVEN,
) -> Decimal:
    if not factor_value.is_finite() or factor_value < 0:
        raise InvalidQuantityError("Emission factor must be finite and non-negative.")
    try:
        with localcontext() as context:
            context.prec = 60
            per_kg = (factor_value / kilograms_per_unit(denominator_unit)).quantize(
                FACTOR_QUANTUM,
                rounding=rounding,
            )
    except InvalidOperation as error:
        raise InvalidQuantityError("Emission factor could not be normalized.") from error
    if not per_kg.is_finite() or abs(per_kg) >= NUMERIC_24_12_LIMIT:
        raise InvalidQuantityError(
            "Normalized emission factor exceeds the supported Numeric(24,12) range."
        )
    return per_kg


def _same_code(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.strip().casefold() == right.strip().casefold()


def _is_supported_numerator(unit: str) -> bool:
    normalized = unit.strip().lower().replace(" ", "")
    return normalized == "kgco2e"


def resolve_factor(
    candidates: Iterable[FactorCandidate],
    *,
    material_code: str,
    product_code: str | None,
    geography: str,
    effective_on: date,
) -> ResolvedFactor:
    """Return one valid factor without silently resolving equal-specificity ties."""
    ranked: list[ResolvedFactor] = []
    requested_geography = geography.strip().casefold()

    for candidate in candidates:
        if candidate.effective_from > effective_on:
            continue
        if candidate.effective_to is not None and candidate.effective_to < effective_on:
            continue
        if candidate.product_code is not None and not _same_code(
            candidate.product_code, product_code
        ):
            continue
        if candidate.material_code is not None and not _same_code(
            candidate.material_code, material_code
        ):
            continue

        factor_geography = candidate.geography.strip().casefold()
        if factor_geography not in {requested_geography, "global"}:
            continue
        if not _is_supported_numerator(candidate.numerator_unit):
            continue

        try:
            per_kg = normalize_factor_to_per_kg(
                candidate.factor_value,
                candidate.denominator_unit,
            )
        except UnsupportedMassUnitError:
            continue

        rank = (
            int(candidate.product_code is not None),
            int(factor_geography == requested_geography),
            int(candidate.material_code is not None),
            int(kilograms_per_unit(candidate.denominator_unit) == Decimal(1)),
        )
        ranked.append(ResolvedFactor(candidate, per_kg, rank))

    if not ranked:
        raise NoMatchingFactorError("No valid emission factor matches the frozen context.")

    highest_rank = max(item.specificity_rank for item in ranked)
    best = [item for item in ranked if item.specificity_rank == highest_rank]
    if len(best) != 1:
        raise AmbiguousFactorError(item.candidate.id for item in best)
    return best[0]


def calculate_emissions(
    normalized_quantity_kg: Decimal,
    factor_kgco2e_per_kg: Decimal,
    *,
    rounding: str = ROUND_HALF_EVEN,
    quantum: Decimal = EMISSIONS_QUANTUM,
) -> Decimal:
    if (
        not normalized_quantity_kg.is_finite()
        or not factor_kgco2e_per_kg.is_finite()
        or normalized_quantity_kg < 0
        or factor_kgco2e_per_kg < 0
    ):
        raise InvalidQuantityError("Quantity and factor must be finite and non-negative.")
    try:
        with localcontext() as context:
            context.prec = 60
            emissions = (normalized_quantity_kg * factor_kgco2e_per_kg).quantize(
                quantum,
                rounding=rounding,
            )
    except InvalidOperation as error:
        raise InvalidQuantityError("Emissions could not be calculated.") from error
    if not emissions.is_finite() or abs(emissions) >= NUMERIC_24_6_LIMIT:
        raise InvalidQuantityError("Emissions exceed the supported Numeric(24,6) range.")
    return emissions


def _validate_confidence_component(name: str, value: Decimal) -> None:
    if not value.is_finite() or value < 0 or value > 1:
        raise InvalidConfidenceConfigurationError(
            f"Confidence component '{name}' must be between 0 and 1."
        )


def calculate_confidence(
    *,
    source_quality: Decimal,
    factor_specificity: Decimal,
    factor_recency: Decimal,
    record_completeness: Decimal,
    weights: Mapping[str, Decimal] = DEFAULT_CONFIDENCE_WEIGHTS,
    rounding: str = ROUND_HALF_EVEN,
) -> ConfidenceBreakdown:
    components = {
        "source_quality": source_quality,
        "factor_specificity": factor_specificity,
        "factor_recency": factor_recency,
        "record_completeness": record_completeness,
    }
    if set(weights) != set(components):
        raise InvalidConfidenceConfigurationError(
            "Confidence weights must define exactly the four supported components."
        )
    for name, value in components.items():
        _validate_confidence_component(name, value)
    if any(not weight.is_finite() or weight < 0 for weight in weights.values()) or sum(
        weights.values(), start=Decimal(0)
    ) != Decimal(1):
        raise InvalidConfidenceConfigurationError(
            "Confidence weights must be non-negative and sum to 1."
        )

    overall = sum(
        (components[name] * weights[name] for name in components),
        start=Decimal(0),
    ).quantize(CONFIDENCE_QUANTUM, rounding=rounding)
    return ConfidenceBreakdown(
        source_quality=source_quality,
        factor_specificity=factor_specificity,
        factor_recency=factor_recency,
        record_completeness=record_completeness,
        overall=overall,
        weights=dict(weights),
    )


def calculate_variance(
    measurement_kgco2e: Decimal,
    baseline_kgco2e: Decimal,
    *,
    rounding: str = ROUND_HALF_EVEN,
) -> VarianceResult:
    variance = (measurement_kgco2e - baseline_kgco2e).quantize(
        EMISSIONS_QUANTUM,
        rounding=rounding,
    )
    if baseline_kgco2e == 0:
        return VarianceResult(variance_kgco2e=variance, variance_pct=None)
    percentage = ((variance / baseline_kgco2e) * Decimal(100)).quantize(
        PERCENT_QUANTUM,
        rounding=rounding,
    )
    return VarianceResult(variance_kgco2e=variance, variance_pct=percentage)


def weighted_average(
    values: Iterable[tuple[Decimal, Decimal]],
    *,
    quantum: Decimal = CONFIDENCE_QUANTUM,
    rounding: str = ROUND_HALF_EVEN,
) -> Decimal:
    materialized = list(values)
    if not materialized:
        raise InvalidQuantityError("At least one value is required for a weighted average.")
    total_weight = sum((weight for _, weight in materialized), start=Decimal(0))
    if total_weight == 0:
        return (
            sum((value for value, _ in materialized), start=Decimal(0)) / Decimal(len(materialized))
        ).quantize(quantum, rounding=rounding)
    return (
        sum((value * weight for value, weight in materialized), start=Decimal(0)) / total_weight
    ).quantize(quantum, rounding=rounding)


def canonical_json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): canonical_json_value(nested)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [canonical_json_value(item) for item in value]
    return value


def sha256_payload(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        canonical_json_value(payload),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
