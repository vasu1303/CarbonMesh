from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.db.models.carbon import ActivityRecord, DataQualityIssue, RawActivityRecord
from app.db.models.core import (
    AuditLog,
    DataSource,
    EvidenceItem,
    ReportingPeriod,
    Site,
    SourceDocument,
)
from app.db.models.procurement import Supplier, SupplierProduct
from app.db.models.semantic import MetricDefinition
from app.modules.imports import service as import_service_module
from app.modules.imports.parser import ImportPayloadError, parse_import_content
from app.modules.imports.schemas import (
    ActivityImportRequest,
    ActivityRow,
    HourlyElectricityRow,
    SupplierImportRequest,
    SupplierProductRow,
)
from app.modules.imports.service import (
    MAX_IMPORT_ROWS,
    MAX_INLINE_ISSUES,
    ImportService,
    InvalidImportContext,
    NormalizedQuantityOutOfRange,
    normalize_quantity,
)
from app.modules.measurement.domain import normalize_mass_to_kg

DEMO_DIR = Path(__file__).resolve().parents[4] / "data" / "demo"


def test_import_issue_order_is_stable_before_truncation() -> None:
    company_id = uuid4()
    now = datetime.now(UTC)
    source = DataSource(
        id=uuid4(), company_id=company_id, is_synthetic=True,
        configuration={"import_type": "activity", "import_status": "completed_with_errors"},
        created_at=now, updated_at=now,
    )
    issues = [
        DataQualityIssue(
            id=UUID(int=index), company_id=company_id, issue_type="validation",
            code="invalid_field_value", severity="error", message="Synthetic invalid row",
            status="open", details={}, created_at=now, updated_at=now,
        )
        for index in range(1, MAX_INLINE_ISSUES + 2)
    ]
    first = import_service_module._build_import_result(source, None, list(reversed(issues)))
    replay = import_service_module._build_import_result(source, None, issues)

    assert first == replay
    assert [issue.id for issue in first.issues] == [issue.id for issue in issues[:-1]]
    assert first.issue_count == MAX_INLINE_ISSUES + 1
    assert first.issues_truncated


def test_csv_parser_preserves_rows_and_strips_bom() -> None:
    parsed = parse_import_content(
        "\ufeffmaterial_code,quantity,unit\nPACKAGING-TRAY,12000,kg\n",
        "text/csv",
    )

    assert parsed.records == [
        {"material_code": "PACKAGING-TRAY", "quantity": "12000", "unit": "kg"}
    ]
    assert parsed.row_numbers == [2]


def test_json_parser_accepts_string_rows_and_products_envelope() -> None:
    from_string = parse_import_content(
        '[{"material_code":"PACKAGING-TRAY","quantity":12000,"unit":"kg"}]',
        "application/json",
    )
    fixture = json.loads((DEMO_DIR / "suppliers.json").read_text(encoding="utf-8"))
    from_envelope = parse_import_content(fixture, "application/json")

    assert from_string.records[0]["quantity"] == Decimal(12000)
    assert from_string.row_numbers == [1]
    assert len(from_envelope.records) == 4
    assert from_envelope.records[2]["product_code"] == "AL-RECYCLED-B"


def test_json_parser_rejects_non_object_rows() -> None:
    with pytest.raises(ImportPayloadError, match="row 2 must be an object"):
        parse_import_content([{"valid": True}, "not-an-object"], "application/json")


def test_supplier_contract_accepts_demo_aliases() -> None:
    fixture = json.loads((DEMO_DIR / "suppliers.json").read_text(encoding="utf-8"))

    product = SupplierProductRow.model_validate(fixture["products"][0])

    assert product.supplier_country_code == "IN"
    assert product.product_name == "Current recycled aluminium billet"
    assert product.pcf_kgco2e_per_unit == Decimal("8.6")


def test_import_envelope_does_not_mutate_content_before_checksum() -> None:
    payload = "material_code,quantity,unit\nPACKAGING-TRAY,12000,kg\n"

    request = ActivityImportRequest(
        company_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        metric_definition_id=uuid4(),
        source_name=" activity fixture ",
        filename=" activity.csv ",
        content_type="text/csv",
        content=payload,
    )

    assert request.content == payload
    assert request.source_name == "activity fixture"
    assert request.filename == "activity.csv"


def test_mass_normalization_uses_decimal_and_fixed_scale() -> None:
    assert normalize_quantity(Decimal("12.5"), "tonnes", "kg") == Decimal("12500.000000")
    assert normalize_quantity(Decimal(10), "lb", "kg") == Decimal("4.535924")

    with pytest.raises(ValueError, match="Unsupported unit conversion"):
        normalize_quantity(Decimal(1), "litre", "kg")


def test_import_normalization_matches_measurement_half_even_tie_breaking() -> None:
    quantity = Decimal("0.0025")

    imported = normalize_quantity(quantity, "g", "kg")

    assert imported == Decimal("0.000002")
    assert imported == normalize_mass_to_kg(quantity, "g")


def test_decimal_contracts_reject_values_that_do_not_fit_database_columns() -> None:
    with pytest.raises(ValueError, match="decimal places|digits"):
        ActivityRow.model_validate(
            {
                "material_code": "PACKAGING-TRAY",
                "quantity": "1.0000001",
                "unit": "kg",
            }
        )
    with pytest.raises(ValueError, match="decimal places|digits"):
        SupplierProductRow.model_validate(
            {
                "supplier_code": "SUPPLIER",
                "supplier_name": "Supplier",
                "country_code": "IN",
                "product_code": "PRODUCT",
                "name": "Product",
                "material_code": "PACKAGING-TRAY",
                "category": "packaging",
                "pcf_kgco2e_per_unit": "1.1234567890123",
                "circularity_score": "50",
                "recycled_content_pct": "50",
                "recyclable_pct": "50",
                "evidence_quality_score": "50",
                "lead_time_days": 1,
                "unit_cost": "1",
                "currency": "USD",
                "effective_from": "2026-01-01",
                "evidence_text": "Evidence",
            }
        )

    with pytest.raises(NormalizedQuantityOutOfRange):
        normalize_quantity(Decimal(999999999999999999), "tonnes", "kg")


def test_supplier_contract_rejects_lead_time_outside_postgresql_integer() -> None:
    with pytest.raises(ValueError, match="less than or equal"):
        SupplierProductRow.model_validate(
            {
                "supplier_code": "SUPPLIER",
                "supplier_name": "Supplier",
                "country_code": "IN",
                "product_code": "PRODUCT",
                "name": "Product",
                "material_code": "PACKAGING-TRAY",
                "category": "packaging",
                "pcf_kgco2e_per_unit": "1.9",
                "circularity_score": "50",
                "recycled_content_pct": "50",
                "recyclable_pct": "50",
                "evidence_quality_score": "50",
                "lead_time_days": 2_147_483_648,
                "unit_cost": "1",
                "currency": "USD",
                "effective_from": "2026-01-01",
                "evidence_text": "Evidence",
            }
        )


def test_currency_validator_rejects_non_ascii_letters_before_database_write() -> None:
    with pytest.raises(ValueError, match="ISO code"):
        ActivityRow.model_validate(
            {
                "material_code": "PACKAGING-TRAY",
                "quantity": "1",
                "unit": "kg",
                "unit_cost": "1",
                "currency": "UŚD",
            }
        )


@pytest.mark.asyncio
async def test_supplier_import_over_row_limit_finishes_as_failed_run() -> None:
    company_id = uuid4()
    request = SupplierImportRequest(
        company_id=company_id,
        source_name="oversize supplier import",
        filename="suppliers.json",
        content_type="application/json",
        content=[{} for _ in range(MAX_IMPORT_ROWS + 1)],
        is_synthetic=True,
    )
    session = FakeSession()
    repository = RowLimitRepository(company_id)

    result = await ImportService(session, repository).import_suppliers(request)

    assert result.status == "failed"
    assert result.accepted_count == 0
    assert result.rejected_count == MAX_IMPORT_ROWS + 1
    assert result.issue_count == 1
    assert result.issues[0].code == "row_limit_exceeded"
    assert repository.source.status == "failed"
    assert session.committed


@pytest.mark.asyncio
async def test_supplier_import_over_payload_limit_finishes_as_failed_run(
    monkeypatch,
) -> None:
    monkeypatch.setattr(import_service_module, "MAX_IMPORT_BYTES", 16)
    company_id = uuid4()
    request = SupplierImportRequest(
        company_id=company_id,
        source_name="oversize supplier import",
        filename="suppliers.json",
        content_type="application/json",
        content={"products": [{}], "padding": "x" * 100},
    )
    session = FakeSession()
    repository = RowLimitRepository(company_id)

    result = await ImportService(session, repository).import_suppliers(request)

    assert result.status == "failed"
    assert result.rejected_count == 1
    assert result.issues[0].code == "payload_limit_exceeded"


@pytest.mark.asyncio
async def test_activity_csv_import_persists_raw_and_normalized_trace() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    metric_id = uuid4()
    product_id = uuid4()
    repository = ActivityRepository(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        metric_id=metric_id,
        product_id=product_id,
    )
    session = FakeSession()
    request = ActivityImportRequest(
        company_id=company_id,
        site_id=site_id,
        reporting_period_id=period_id,
        metric_definition_id=metric_id,
        source_name="demo activity",
        filename="activity.csv",
        content_type="text/csv",
        content=(DEMO_DIR / "activity.csv").read_text(encoding="utf-8"),
        is_synthetic=True,
    )

    result = await ImportService(session, repository).import_activity(request)

    assert result.status == "completed"
    assert result.accepted_count == 1
    assert result.rejected_count == 0
    assert repository.raw_records[0].row_number == 2
    assert repository.raw_records[0].import_status == "accepted"
    assert repository.activities[0].normalized_quantity == Decimal("10000.000000")
    assert repository.activities[0].supplier_product_id == product_id
    assert repository.source.status == "ready"
    assert session.committed


@pytest.mark.asyncio
async def test_activity_import_rejects_non_activity_metric_context() -> None:
    company_id = uuid4()
    repository = ActivityRepository(
        company_id=company_id,
        site_id=uuid4(),
        period_id=uuid4(),
        metric_id=uuid4(),
        product_id=uuid4(),
    )
    repository.metric.key = "supplier.product_carbon_footprint"
    request = ActivityImportRequest(
        company_id=company_id,
        site_id=repository.site.id,
        reporting_period_id=repository.period.id,
        metric_definition_id=repository.metric.id,
        source_name="invalid metric activity",
        filename="activity.csv",
        content_type="text/csv",
        content=(DEMO_DIR / "activity.csv").read_text(encoding="utf-8"),
    )

    with pytest.raises(InvalidImportContext, match="purchased-material mass"):
        await ImportService(FakeSession(), repository).import_activity(request)


@pytest.mark.asyncio
async def test_activity_import_rejects_missing_supplier_product_reference() -> None:
    company_id = uuid4()
    repository = ActivityRepository(
        company_id=company_id,
        site_id=uuid4(),
        period_id=uuid4(),
        metric_id=uuid4(),
        product_id=uuid4(),
    )
    request = ActivityImportRequest(
        company_id=company_id,
        site_id=repository.site.id,
        reporting_period_id=repository.period.id,
        metric_definition_id=repository.metric.id,
        source_name="missing supplier activity",
        filename="activity.json",
        content_type="application/json",
        content={
            "material_code": "PACKAGING-TRAY",
            "quantity": "12000",
            "unit": "kg",
            "activity_date": "2026-09-15",
        },
    )

    result = await ImportService(FakeSession(), repository).import_activity(request)

    assert result.status == "failed"
    assert result.accepted_count == 0
    assert result.rejected_count == 1
    assert result.issues[0].code == "supplier_product_required"
    assert repository.activities == []


@pytest.mark.asyncio
async def test_import_result_caps_inline_issues_but_preserves_exact_count() -> None:
    company_id = uuid4()
    request = SupplierImportRequest(
        company_id=company_id,
        source_name="invalid suppliers",
        filename="suppliers.json",
        content_type="application/json",
        content=[{} for _ in range(MAX_INLINE_ISSUES + 1)],
    )

    result = await ImportService(FakeSession(), RowLimitRepository(company_id)).import_suppliers(
        request
    )

    assert result.issue_count > MAX_INLINE_ISSUES
    assert result.returned_issue_count == MAX_INLINE_ISSUES
    assert len(result.issues) == MAX_INLINE_ISSUES
    assert result.issues_truncated


@pytest.mark.asyncio
async def test_supplier_json_string_metadata_is_jsonb_safe() -> None:
    company_id = uuid4()
    repository = SupplierRepository(company_id)
    request = SupplierImportRequest(
        company_id=company_id,
        source_name="supplier metadata",
        filename="supplier.json",
        content_type="application/json",
        content=json.dumps(
            {
                "products": [
                    {
                        "supplier_code": "SUPPLIER",
                        "supplier_name": "Supplier",
                        "country_code": "IN",
                        "product_code": "PRODUCT",
                        "name": "Product",
                        "material_code": "PACKAGING-TRAY",
                        "category": "packaging",
                        "pcf_kgco2e_per_unit": 1.9,
                        "circularity_score": 75,
                        "recycled_content_pct": 60,
                        "recyclable_pct": 90,
                        "evidence_quality_score": 92,
                        "lead_time_days": 10,
                        "unit_cost": 1.03,
                        "currency": "USD",
                        "effective_from": "2026-01-01",
                        "evidence_text": "Synthetic supplier evidence.",
                        "supplier_metadata": {"risk_rating": 4.5},
                        "evidence_metadata": {"page": 2, "confidence": 0.95},
                    }
                ]
            }
        ),
    )

    result = await ImportService(FakeSession(), repository).import_suppliers(request)

    assert result.status == "completed"
    assert repository.supplier_metadata == {"risk_rating": "4.5"}
    assert repository.evidence_metadata["page"] == "2"
    assert repository.evidence_metadata["confidence"] == "0.95"


@pytest.mark.asyncio
async def test_supplier_import_rejects_product_for_inactive_supplier() -> None:
    company_id = uuid4()
    repository = InactiveSupplierRepository(company_id)
    fixture = json.loads((DEMO_DIR / "suppliers.json").read_text(encoding="utf-8"))
    request = SupplierImportRequest(
        company_id=company_id,
        source_name="inactive supplier",
        filename="supplier.json",
        content_type="application/json",
        content=fixture["products"][0],
    )

    result = await ImportService(FakeSession(), repository).import_suppliers(request)

    assert result.status == "failed"
    assert result.accepted_count == 0
    assert result.rejected_count == 1
    assert result.issues[0].code == "supplier_inactive"


class FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class RowLimitRepository:
    def __init__(self, company_id) -> None:
        self.company_id = company_id
        self.source: DataSource

    async def company_exists(self, company_id) -> bool:
        return company_id == self.company_id

    async def create_data_source(self, **values: Any) -> DataSource:
        now = datetime.now(UTC)
        self.source = DataSource(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            **values,
        )
        return self.source

    async def find_document_by_checksum(self, company_id, checksum) -> None:
        return None

    async def create_source_document(self, **values: Any) -> SourceDocument:
        now = datetime.now(UTC)
        return SourceDocument(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            **values,
        )

    async def create_issue(self, **values: Any) -> DataQualityIssue:
        now = datetime.now(UTC)
        return DataQualityIssue(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            status="open",
            **values,
        )

    async def create_audit_log(self, **values: Any) -> AuditLog:
        return AuditLog(id=uuid4(), created_at=datetime.now(UTC), **values)


class ActivityRepository(RowLimitRepository):
    def __init__(
        self,
        *,
        company_id,
        site_id,
        period_id,
        metric_id,
        product_id,
    ) -> None:
        super().__init__(company_id)
        now = datetime.now(UTC)
        self.site = Site(
            id=site_id,
            company_id=company_id,
            code="PLANT-B",
            name="Plant B",
            country_code="IN",
            timezone="Asia/Kolkata",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.period = ReportingPeriod(
            id=period_id,
            company_id=company_id,
            name="Q3 2026",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 9, 30),
            status="open",
            created_at=now,
            updated_at=now,
        )
        self.metric = MetricDefinition(
            id=metric_id,
            company_id=company_id,
            semantic_entity_id=None,
            key="activity.purchased_material_mass",
            version="1.0.0",
            name="Purchased material mass",
            canonical_unit="kg",
            dimensions={},
            handler="measurement.mass",
            method_version="1.0.0",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.product = SupplierProduct(
            id=product_id,
            company_id=company_id,
            material_code="RECYCLED-ALUMINIUM",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.raw_records: list[RawActivityRecord] = []
        self.activities: list[ActivityRecord] = []

    async def get_site(self, company_id, site_id) -> Site | None:
        return self.site if (company_id, site_id) == (self.company_id, self.site.id) else None

    async def get_reporting_period(self, company_id, period_id) -> ReportingPeriod | None:
        if (company_id, period_id) == (self.company_id, self.period.id):
            return self.period
        return None

    async def get_metric_definition(self, company_id, metric_id) -> MetricDefinition | None:
        if (company_id, metric_id) == (self.company_id, self.metric.id):
            return self.metric
        return None

    async def get_supplier_product_by_codes(
        self, company_id, supplier_code, product_code
    ) -> SupplierProduct | None:
        if (
            company_id == self.company_id
            and supplier_code == "MAVERICK-CURRENT"
            and product_code == "AL-CURRENT"
        ):
            return self.product
        return None

    async def get_supplier_product(self, company_id, product_id) -> SupplierProduct | None:
        if (company_id, product_id) == (self.company_id, self.product.id):
            return self.product
        return None

    async def create_raw_activity(self, **values: Any) -> RawActivityRecord:
        raw = RawActivityRecord(
            id=uuid4(),
            created_at=datetime.now(UTC),
            import_status="pending",
            **values,
        )
        self.raw_records.append(raw)
        return raw

    async def create_activity(self, **values: Any) -> ActivityRecord:
        now = datetime.now(UTC)
        activity = ActivityRecord(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            **values,
        )
        self.activities.append(activity)
        return activity

    async def list_hourly_activity_payloads(self, **context) -> list[dict[str, Any]]:
        return [raw.raw_payload for raw in self.raw_records if raw.import_status == "accepted"]


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-07-01T00:00:00",
        "2026-07-01T00:30:00Z",
        "2026-07-01T00:00:01Z",
        "2026-07-01T00:00:00.000001Z",
        "not-a-timestamp",
        1782864000,
        "1782864000",
    ],
)
def test_hourly_row_rejects_ambiguous_or_unaligned_timestamps(timestamp) -> None:
    with pytest.raises(ValueError):
        HourlyElectricityRow.model_validate({"timestamp": timestamp, "kwh": "10"})


def test_hourly_row_normalizes_offset_and_rejects_conflicting_aliases() -> None:
    row = HourlyElectricityRow.model_validate(
        {
            "timestamp": "2026-07-01T05:30:00+05:30",
            "kwh": "0.123456",
        }
    )
    assert row.timestamp == datetime(2026, 7, 1, tzinfo=UTC)
    assert row.quantity == Decimal("0.123456")
    assert row.material_code == "ELECTRICITY"
    assert row.activity_date == date(2026, 7, 1)
    for payload in (
        {"timestamp": "2026-07-01T00:00Z", "kwh": "10", "quantity": "11"},
        {"timestamp": "2026-07-01T00:00Z", "interval_start": "2026-07-01T01:00Z", "kwh": "10"},
        {"timestamp": "2026-07-01T00:00Z", "kwh": "10", "unit": "MWh"},
    ):
        with pytest.raises(ValueError):
            HourlyElectricityRow.model_validate(payload)


def test_energy_normalization_is_decimal_and_dimensionally_safe() -> None:
    assert normalize_quantity(Decimal("0.123456"), "MWh", "kWh") == Decimal("123.456000")
    assert normalize_quantity(Decimal(125), "Wh", "kWh") == Decimal("0.125000")
    with pytest.raises(ValueError):
        normalize_quantity(Decimal(1), "kg", "kWh")


def _hourly_repository_and_request(content, **request_overrides):
    repository = ActivityRepository(
        company_id=uuid4(),
        site_id=uuid4(),
        period_id=uuid4(),
        metric_id=uuid4(),
        product_id=uuid4(),
    )
    repository.metric.key = "activity.electricity_consumption"
    repository.metric.canonical_unit = "kWh"
    request = ActivityImportRequest(
        company_id=repository.company_id,
        site_id=repository.site.id,
        reporting_period_id=repository.period.id,
        metric_definition_id=repository.metric.id,
        source_name="Synthetic hourly electricity",
        filename="electricity.json",
        content_type="application/json",
        content=content,
        is_synthetic=True,
        **request_overrides,
    )
    return repository, request


@pytest.mark.asyncio
async def test_hourly_import_preserves_raw_and_normalizes_energy() -> None:
    payload = [
        {"timestamp": "2026-07-01T05:30:00+05:30", "kwh": "10.123456"},
        {"timestamp": "2026-07-01T01:00Z", "quantity": "0.25", "unit": "MWh"},
    ]
    repository, request = _hourly_repository_and_request(payload)
    result = await ImportService(FakeSession(), repository).import_activity(request)
    assert result.accepted_count == 2
    assert result.status == "completed"
    assert [raw.raw_payload for raw in repository.raw_records] == payload
    assert [activity.normalized_quantity for activity in repository.activities] == [
        Decimal("10.123456"),
        Decimal("250.000000"),
    ]
    assert all(activity.supplier_product_id is None for activity in repository.activities)
    assert repository.source.configuration["expected_intervals"] == 2


@pytest.mark.asyncio
async def test_hourly_import_rejects_all_duplicate_intervals_and_records_gaps() -> None:
    payload = [
        {"row_key": "a", "timestamp": "2026-07-01T00:00Z", "kwh": "10"},
        {"row_key": "b", "timestamp": "2026-07-01T05:30+05:30", "kwh": "11"},
        {"row_key": "c", "timestamp": "2026-07-01T02:00Z", "kwh": "12"},
    ]
    repository, request = _hourly_repository_and_request(payload)
    result = await ImportService(FakeSession(), repository).import_activity(request)
    assert result.accepted_count == 1
    assert result.rejected_count == 2
    assert [raw.import_status for raw in repository.raw_records] == [
        "rejected",
        "rejected",
        "accepted",
    ]
    assert [issue.code for issue in result.issues].count("duplicate_timestamp") == 2
    missing = next(issue for issue in result.issues if issue.code == "missing_interval")
    assert missing.details["missing_count"] == 2
    assert missing.details["blocks_verification"] is True


@pytest.mark.asyncio
async def test_hourly_import_checks_declared_boundary_gaps_and_existing_rows() -> None:
    repository, request = _hourly_repository_and_request(
        [{"timestamp": "2026-07-01T01:00Z", "kwh": "12"}],
        interval_start="2026-07-01T00:00Z",
        interval_end="2026-07-01T03:00Z",
    )
    service = ImportService(FakeSession(), repository)
    result = await service.import_activity(request)
    assert result.accepted_count == 1
    assert [issue.code for issue in result.issues] == ["missing_interval", "missing_interval"]
    repeated = await service.import_activity(request)
    assert repeated.accepted_count == 0
    assert "duplicate_timestamp" in {issue.code for issue in repeated.issues}
    assert len(repository.activities) == 1


@pytest.mark.asyncio
async def test_hourly_import_preserves_invalid_rows_without_accepting_them() -> None:
    payload = [
        {"timestamp": "2026-07-01T00:00:00", "kwh": "12"},
        {"timestamp": "2026-07-01T01:00Z", "kwh": "-1"},
        {"timestamp": "2026-07-01T02:00Z", "kwh": "10", "site_code": "OTHER"},
    ]
    repository, request = _hourly_repository_and_request(payload)
    result = await ImportService(FakeSession(), repository).import_activity(request)
    assert result.status == "failed"
    assert result.rejected_count == 3
    assert [raw.raw_payload for raw in repository.raw_records] == payload
    assert all(issue.details["blocks_verification"] for issue in result.issues)
    assert not repository.activities


@pytest.mark.asyncio
async def test_activity_import_rolls_back_when_persistence_fails(monkeypatch) -> None:
    repository, request = _hourly_repository_and_request(
        [{"timestamp": "2026-07-01T00:00Z", "kwh": "12"}]
    )
    session = FakeSession()

    async def fail(**values):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(repository, "create_activity", fail)
    with pytest.raises(RuntimeError, match="simulated write failure"):
        await ImportService(session, repository).import_activity(request)
    assert session.rolled_back
    assert not session.committed


class SupplierRepository(RowLimitRepository):
    def __init__(self, company_id) -> None:
        super().__init__(company_id)
        self.supplier: Supplier | None = None
        self.supplier_metadata: dict[str, Any] = {}
        self.evidence_metadata: dict[str, Any] = {}

    async def get_supplier(self, company_id, supplier_code) -> None:
        return None

    async def create_supplier(self, **values: Any) -> Supplier:
        now = datetime.now(UTC)
        model_values = dict(values)
        self.supplier_metadata = model_values.pop("metadata")
        self.supplier = Supplier(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            status="active",
            supplier_metadata=self.supplier_metadata,
            **model_values,
        )
        return self.supplier

    async def get_supplier_product_for_supplier(
        self, company_id, supplier_id, product_code
    ) -> None:
        return None

    async def create_evidence_item(self, **values: Any) -> EvidenceItem:
        now = datetime.now(UTC)
        model_values = dict(values)
        self.evidence_metadata = model_values.pop("metadata")
        return EvidenceItem(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            evidence_metadata=self.evidence_metadata,
            **model_values,
        )

    async def create_supplier_product(self, **values: Any) -> SupplierProduct:
        now = datetime.now(UTC)
        return SupplierProduct(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            **values,
        )


class InactiveSupplierRepository(SupplierRepository):
    def __init__(self, company_id) -> None:
        super().__init__(company_id)
        now = datetime.now(UTC)
        self.supplier = Supplier(
            id=uuid4(),
            company_id=company_id,
            supplier_code="MAVERICK-CURRENT",
            name="Maverick Metals Baseline (Synthetic)",
            country_code="IN",
            status="inactive",
            supplier_metadata={},
            created_at=now,
            updated_at=now,
        )

    async def get_supplier(self, company_id, supplier_code) -> Supplier | None:
        if company_id == self.company_id and supplier_code == self.supplier.supplier_code:
            return self.supplier
        return None
