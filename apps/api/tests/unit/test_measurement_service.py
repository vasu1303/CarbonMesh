from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.measurement.repository import (
    ActivitySource,
    MeasurementDetailBundle,
    PersistedMeasurement,
)
from app.modules.measurement.schemas import MeasurementCalculateRequest
from app.modules.measurement.service import MeasurementService, MeasurementServiceError


class FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeMeasurementRepository:
    def __init__(self, *, activities: list[ActivitySource] | None = None) -> None:
        self.company_id = uuid4()
        self.site = SimpleNamespace(id=uuid4(), name="Plant B", country_code="IN")
        self.period = SimpleNamespace(
            id=uuid4(),
            name="Q3 2026",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 9, 30),
        )
        self.activity_metric = SimpleNamespace(
            id=uuid4(),
            key="activity.purchased_material_mass",
            version="1.0.0",
            canonical_unit="kg",
        )
        self.output_metric = SimpleNamespace(
            id=uuid4(),
            key="emissions.scope3.category1",
            version="1.0.0",
            canonical_unit="kgCO2e",
            method_version="1.0.0",
        )
        self.method = SimpleNamespace(
            id=uuid4(),
            key="measurement.scope3.category1.mass_factor",
            version="1.0.0",
            code_version="test",
            configuration={},
        )
        self.activities = activities or []
        self.factors: list[SimpleNamespace] = []
        self.evidence_checksums: dict[object, str] = {}
        self.actor_is_valid = True
        self.lock_acquired = False
        self.persisted_plan = None
        self.detail_bundle = None

    async def get_site(self, **_kwargs):
        return self.site

    async def company_exists(self, **_kwargs) -> bool:
        return True

    async def get_reporting_period(self, **_kwargs):
        return self.period

    async def list_active_metrics(self, *, key: str, **_kwargs):
        return [self.activity_metric if key.startswith("activity.") else self.output_metric]

    async def list_active_methods(self, **_kwargs):
        return [self.method]

    async def actor_exists(self, **_kwargs) -> bool:
        return self.actor_is_valid

    async def agent_run_exists(self, **_kwargs) -> bool:
        return True

    async def acquire_calculation_lock(self, **_kwargs) -> None:
        self.lock_acquired = True

    async def list_activities(self, **_kwargs):
        return self.activities

    async def list_factor_candidates(self, **_kwargs):
        return self.factors

    async def get_evidence_checksums(self, **_kwargs):
        return self.evidence_checksums

    async def get_baseline(self, **_kwargs):
        return None

    async def find_measurement_id_by_input_hash(self, **_kwargs):
        return None

    async def get_latest_verified_measurement(self, **_kwargs):
        return None

    async def persist_measurement(self, plan):
        self.persisted_plan = plan
        measurement_id = uuid4()
        run_id = uuid4()
        ledger_event_id = uuid4()
        audit_id = uuid4()
        item = plan.items[0]
        evidence = SimpleNamespace(
            id=item.factor.evidence_item_id,
            source_document_id=uuid4(),
            evidence_type="synthetic_factor",
            locator="synthetic:factor-current",
            checksum=self.evidence_checksums[item.factor.evidence_item_id],
        )
        source_document = SimpleNamespace(
            id=evidence.source_document_id,
            data_source_id=uuid4(),
            filename="synthetic-factor.txt",
            checksum="d" * 64,
        )
        calculation = SimpleNamespace(
            id=uuid4(),
            activity_record_id=item.source.activity.id,
            emission_factor_id=item.factor.id,
            normalized_quantity=item.normalized_quantity_kg,
            factor_value=item.factor_kgco2e_per_kg,
            emissions_kgco2e=item.emissions_kgco2e,
            formula=item.formula,
            output_hash=item.output_hash,
        )
        measurement = SimpleNamespace(
            id=measurement_id,
            company_id=plan.company_id,
            calculation_run_id=run_id,
            site_id=plan.context.site.id,
            reporting_period_id=plan.context.reporting_period.id,
            metric_definition_id=plan.context.output_metric.id,
            ledger_event_id=ledger_event_id,
            value_kgco2e=plan.value_kgco2e,
            unit="kgCO2e",
            confidence=plan.confidence.overall,
            status="verified",
            formula=plan.formula,
            output_hash=plan.output_hash,
            verified_at=datetime(2026, 9, 30, tzinfo=UTC),
            created_at=datetime(2026, 9, 30, tzinfo=UTC),
        )
        run = SimpleNamespace(
            id=run_id,
            method_definition_id=plan.context.method.id,
            method_version=plan.context.method.version,
            code_version=plan.context.method.code_version,
            rounding_policy=plan.rounding_policy,
            input_hash=plan.input_hash,
            output_hash=plan.output_hash,
            status="completed",
            started_at=datetime(2026, 9, 30, tzinfo=UTC),
            completed_at=datetime(2026, 9, 30, tzinfo=UTC),
            summary={
                "confidence": asdict(plan.confidence),
                "baseline": None,
            },
        )
        self.detail_bundle = MeasurementDetailBundle(
            measurement=measurement,
            calculation_run=run,
            metric=plan.context.output_metric,
            method=plan.context.method,
            site=plan.context.site,
            reporting_period=plan.context.reporting_period,
            audit_log_id=audit_id,
            calculations=(
                (
                    calculation,
                    item.source.activity,
                    item.source.product_code,
                    item.source.raw_activity,
                    item.factor,
                    evidence,
                    source_document,
                ),
            ),
            baseline=None,
            variance_alert=None,
        )
        return PersistedMeasurement(
            measurement_id=measurement_id,
            calculation_run_id=run_id,
            ledger_event_id=ledger_event_id,
            variance_alert_id=None,
            audit_log_id=audit_id,
        )

    async def get_detail_bundle(self, **_kwargs):
        return self.detail_bundle


def _request(repository: FakeMeasurementRepository, **changes) -> MeasurementCalculateRequest:
    values = {
        "company_id": repository.company_id,
        "site_id": repository.site.id,
        "reporting_period_id": repository.period.id,
        "material_code": "PACKAGING_TRAY",
        "trace_id": "measurement-test-trace",
    }
    values.update(changes)
    return MeasurementCalculateRequest(**values)


def _valid_activity_source() -> ActivitySource:
    raw_activity_id = uuid4()
    product_id = uuid4()
    activity = SimpleNamespace(
        id=uuid4(),
        raw_activity_record_id=raw_activity_id,
        supplier_product_id=product_id,
        material_code="PACKAGING_TRAY",
        activity_date=date(2026, 8, 15),
        quantity=Decimal(12000),
        unit="kg",
        normalized_quantity=Decimal(12000),
        normalized_unit="kg",
        unit_cost=Decimal("1.000000"),
        currency="USD",
    )
    raw_activity = SimpleNamespace(
        id=raw_activity_id,
        data_source_id=uuid4(),
        source_document_id=uuid4(),
        row_key="plant-b-packaging-q3-2026",
        row_number=1,
        checksum="a" * 64,
    )
    return ActivitySource(
        activity=activity,
        product_code="TRAY-CURRENT",
        raw_activity=raw_activity,
    )


def _valid_factor() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        factor_code="TRAY-CURRENT-PCF",
        version="1.0.0",
        name="Synthetic current tray factor",
        material_code="PACKAGING_TRAY",
        product_code="TRAY-CURRENT",
        geography="IN",
        factor_value=Decimal("2.8"),
        numerator_unit="kgCO2e",
        denominator_unit="kg",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        source_quality=Decimal("0.90"),
        factor_specificity=Decimal("0.95"),
        factor_recency=Decimal("0.85"),
        evidence_item_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_calculate_happy_path_returns_demo_result_and_persists_once() -> None:
    source = _valid_activity_source()
    repository = FakeMeasurementRepository(activities=[source])
    factor = _valid_factor()
    repository.factors = [factor]
    repository.evidence_checksums = {factor.evidence_item_id: "b" * 64}
    session = FakeSession()
    service = MeasurementService(session, repository)  # type: ignore[arg-type]

    result = await service.calculate(_request(repository))

    assert result.value_kgco2e == Decimal(33600)
    assert result.unit == "kgCO2e"
    assert result.status == "verified"
    assert result.terminal_state == "completed"
    assert result.idempotent is False
    assert session.committed
    assert not session.rolled_back
    assert repository.lock_acquired
    assert repository.persisted_plan is not None
    assert repository.persisted_plan.value_kgco2e == Decimal(33600)
    assert repository.persisted_plan.items[0].factor.id == factor.id
    assert result.inputs[0].source_row_key == "plant-b-packaging-q3-2026"
    assert result.factors[0].evidence.checksum == "b" * 64


@pytest.mark.asyncio
async def test_detail_keeps_frozen_absence_of_baseline() -> None:
    source = _valid_activity_source()
    repository = FakeMeasurementRepository(activities=[source])
    factor = _valid_factor()
    repository.factors = [factor]
    repository.evidence_checksums = {factor.evidence_item_id: "b" * 64}
    service = MeasurementService(FakeSession(), repository)  # type: ignore[arg-type]

    await service.calculate(_request(repository))

    assert repository.detail_bundle is not None
    later_baseline = SimpleNamespace(
        id=uuid4(),
        name="Baseline added after calculation",
        value_kgco2e=Decimal(30000),
        unit="kgCO2e",
    )
    bundle = replace(repository.detail_bundle, baseline=later_baseline)

    detail = service._detail_from_bundle(bundle)

    assert detail.baseline is None


@pytest.mark.asyncio
async def test_calculate_returns_typed_error_for_equally_specific_factors() -> None:
    source = _valid_activity_source()
    repository = FakeMeasurementRepository(activities=[source])
    first = _valid_factor()
    second = _valid_factor()
    repository.factors = [first, second]
    repository.evidence_checksums = {
        first.evidence_item_id: "b" * 64,
        second.evidence_item_id: "c" * 64,
    }
    service = MeasurementService(FakeSession(), repository)  # type: ignore[arg-type]

    with pytest.raises(MeasurementServiceError) as caught:
        await service.calculate(_request(repository))

    assert caught.value.status_code == 409
    assert caught.value.code == "emission_factor_ambiguous"
    assert caught.value.terminal_state == "needs_clarification"
    assert set(caught.value.field_details["factor_ids"]) == {str(first.id), str(second.id)}
    assert repository.persisted_plan is None


@pytest.mark.asyncio
async def test_calculate_returns_explicit_no_data_state_without_writing() -> None:
    session = FakeSession()
    repository = FakeMeasurementRepository()
    service = MeasurementService(session, repository)  # type: ignore[arg-type]

    with pytest.raises(MeasurementServiceError) as caught:
        await service.calculate(_request(repository))

    assert caught.value.status_code == 404
    assert caught.value.code == "no_activity_data"
    assert caught.value.terminal_state == "no_data"
    assert caught.value.trace_id == "measurement-test-trace"
    assert not session.committed
    assert not session.rolled_back


@pytest.mark.asyncio
async def test_calculate_rejects_unsupported_activity_unit_before_factor_selection() -> None:
    activity = SimpleNamespace(
        id=uuid4(),
        raw_activity_record_id=uuid4(),
        supplier_product_id=None,
        material_code="PACKAGING_TRAY",
        activity_date=date(2026, 8, 15),
        quantity=Decimal(12000),
        unit="cubic-metre",
        normalized_quantity=Decimal(12000),
        normalized_unit="kg",
        unit_cost=Decimal(1),
        currency="USD",
    )
    raw_activity = SimpleNamespace(
        id=activity.raw_activity_record_id,
        data_source_id=uuid4(),
        source_document_id=uuid4(),
        row_key="activity-row-1",
        row_number=1,
        checksum="a" * 64,
    )
    repository = FakeMeasurementRepository(
        activities=[
            ActivitySource(
                activity=activity,
                product_code=None,
                raw_activity=raw_activity,
            )
        ]
    )
    service = MeasurementService(FakeSession(), repository)  # type: ignore[arg-type]

    with pytest.raises(MeasurementServiceError) as caught:
        await service.calculate(_request(repository))

    assert caught.value.status_code == 422
    assert caught.value.code == "unsupported_mass_unit"
    assert caught.value.terminal_state == "unsupported"
    assert caught.value.field_details == {
        "activity_record_id": str(activity.id),
        "unit": "cubic-metre",
    }


@pytest.mark.asyncio
async def test_calculate_returns_typed_error_for_numeric_overflow() -> None:
    source = _valid_activity_source()
    source.activity.quantity = Decimal("999999999999999999.999999")
    source.activity.unit = "tonnes"
    repository = FakeMeasurementRepository(activities=[source])
    factor = _valid_factor()
    repository.factors = [factor]
    repository.evidence_checksums = {factor.evidence_item_id: "b" * 64}
    service = MeasurementService(FakeSession(), repository)  # type: ignore[arg-type]

    with pytest.raises(MeasurementServiceError) as caught:
        await service.calculate(_request(repository))

    assert caught.value.status_code == 422
    assert caught.value.code == "activity_quantity_out_of_range"
    assert caught.value.terminal_state == "failed_validation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configuration",
    [
        [],
        {"confidence_weights": []},
        {"confidence_weights": {"source_qualty": "0.40"}},
        {"confidence_weights": {"source_quality": "NaN"}},
    ],
)
async def test_calculate_returns_typed_error_for_malformed_method_configuration(
    configuration: object,
) -> None:
    source = _valid_activity_source()
    repository = FakeMeasurementRepository(activities=[source])
    repository.method.configuration = configuration
    factor = _valid_factor()
    repository.factors = [factor]
    repository.evidence_checksums = {factor.evidence_item_id: "b" * 64}
    service = MeasurementService(FakeSession(), repository)  # type: ignore[arg-type]

    with pytest.raises(MeasurementServiceError) as caught:
        await service.calculate(_request(repository))

    assert caught.value.status_code == 500
    assert caught.value.code == "invalid_measurement_method"
    assert caught.value.terminal_state == "failed_validation"


@pytest.mark.asyncio
async def test_calculate_validates_actor_tenant_scope_before_reading_activity() -> None:
    repository = FakeMeasurementRepository()
    repository.actor_is_valid = False
    actor_id = uuid4()
    service = MeasurementService(FakeSession(), repository)  # type: ignore[arg-type]

    with pytest.raises(MeasurementServiceError) as caught:
        await service.calculate(_request(repository, actor_id=actor_id))

    assert caught.value.status_code == 404
    assert caught.value.code == "measurement_context_not_found"
    assert caught.value.field_details == {"actor_id": str(actor_id)}
