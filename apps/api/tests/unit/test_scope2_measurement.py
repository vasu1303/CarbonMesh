from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.measurement.domain import (
    MeasurementDomainError,
    calculate_confidence,
    calculate_confidence_v2,
    calculate_scope2_emissions,
    hourly_timestamp,
    normalize_energy_to_kwh,
)
from app.modules.measurement.schemas import MeasurementCalculateRequest
from app.modules.measurement.service import MeasurementService, MeasurementServiceError
from tests.unit.test_measurement_service import (
    FakeMeasurementRepository,
    FakeSession,
    _request,
    _valid_activity_source,
    _valid_factor,
)


def test_scope2_uses_exact_decimal_conversion_and_half_even_rounding():
    assert normalize_energy_to_kwh(Decimal("0.500001"), "MWh") == Decimal("500.001000")
    assert calculate_scope2_emissions(Decimal("500.001"), Decimal("475.123456789")) == Decimal(
        "237.562204"
    )
    assert calculate_scope2_emissions(Decimal("0.001"), Decimal("0.5")) == Decimal("0.000000")
    assert calculate_scope2_emissions(Decimal("0.003"), Decimal("0.5")) == Decimal("0.000002")


@pytest.mark.parametrize("quantity,intensity", [("-1", "1"), ("1", "NaN"), ("1e30", "1000")])
def test_scope2_rejects_invalid_or_overflow_values(quantity, intensity):
    with pytest.raises(MeasurementDomainError):
        calculate_scope2_emissions(Decimal(quantity), Decimal(intensity))


def test_hourly_timestamp_normalizes_offsets_without_rounding():
    assert hourly_timestamp({"timestamp": "2026-07-01T05:30:00+05:30"}) == datetime(
        2026, 7, 1, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"timestamp": "2026-07-01T00:00:00"},
        {"timestamp": "2026-07-01T00:15:00Z"},
        {"timestamp": "2026-07-01T00:00:00Z", "interval_start": "2026-07-01T01:00:00Z"},
    ],
)
def test_hourly_timestamp_rejects_missing_naive_unaligned_and_conflicting_times(payload):
    with pytest.raises(MeasurementDomainError):
        hourly_timestamp(payload)


def test_confidence_v2_does_not_reinterpret_persisted_v1_components():
    old = calculate_confidence(
        source_quality=Decimal("0.7"),
        factor_specificity=Decimal(1),
        factor_recency=Decimal(1),
        record_completeness=Decimal(1),
    )
    new = calculate_confidence_v2(
        source_quality=Decimal("0.7"),
        method_fit=Decimal(1),
        temporal_match=Decimal(1),
        completeness=Decimal(1),
    )
    assert old.overall == Decimal("0.88000")
    assert new.overall == Decimal("0.89500")
    assert new.version == "2.0.0"


def test_scope2_request_defaults_are_explicit_and_do_not_change_material_defaults():
    scope2 = MeasurementCalculateRequest(
        company_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        output_metric_key="emissions.scope2.location_based",
    )
    assert scope2.material_code == "ELECTRICITY"
    assert scope2.activity_metric_key == "activity.electricity_consumption"
    assert scope2.method_key == "measurement.scope2.location_based.hourly"
    material = MeasurementCalculateRequest(
        company_id=uuid4(), site_id=uuid4(), reporting_period_id=uuid4(), material_code="ALUMINIUM"
    )
    assert material.method_key == "measurement.scope3.category1.mass_factor"


@pytest.mark.asyncio
async def test_new_material_method_uses_confidence_v2_and_records_its_version():
    repository = FakeMeasurementRepository(activities=[_valid_activity_source()])
    factor = _valid_factor()
    repository.factors = [factor]
    repository.evidence_checksums = {factor.evidence_item_id: "b" * 64}
    repository.method.version = "2.0.0"
    repository.method.configuration = {"confidence_method": "measurement-confidence-v2"}
    repository.output_metric.method_version = "2.0.0"
    result = await MeasurementService(FakeSession(), repository).calculate(_request(repository))
    assert result.value_kgco2e == Decimal(33600)
    assert result.confidence == Decimal("0.92250")
    assert result.confidence_breakdown.version == "2.0.0"
    assert result.calculation_run.method_version == "2.0.0"


@pytest.mark.asyncio
async def test_confidence_upgrade_cannot_reinterpret_method_v1():
    repository = FakeMeasurementRepository(activities=[_valid_activity_source()])
    factor = _valid_factor()
    repository.factors = [factor]
    repository.evidence_checksums = {factor.evidence_item_id: "b" * 64}
    repository.method.configuration = {"confidence_method": "measurement-confidence-v2"}
    with pytest.raises(MeasurementServiceError) as caught:
        await MeasurementService(FakeSession(), repository).calculate(_request(repository))
    assert caught.value.code == "invalid_measurement_method"
    assert repository.persisted_plan is None
