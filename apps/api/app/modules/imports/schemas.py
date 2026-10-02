from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ActivityQuantity = Annotated[
    Decimal,
    Field(ge=0, allow_inf_nan=False, max_digits=24, decimal_places=6),
]
Money = Annotated[
    Decimal,
    Field(ge=0, allow_inf_nan=False, max_digits=20, decimal_places=6),
]
ProductCarbonFootprint = Annotated[
    Decimal,
    Field(ge=0, allow_inf_nan=False, max_digits=24, decimal_places=12),
]
Score = Annotated[
    Decimal,
    Field(ge=0, le=100, allow_inf_nan=False, max_digits=7, decimal_places=4),
]
ImportContent = str | list[Any] | dict[str, Any]


def validate_hourly_timestamp(value: datetime) -> datetime:
    """Require an explicit offset and an exact UTC hour; never infer local time."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include an explicit UTC offset")
    normalized = value.astimezone(UTC)
    if normalized.minute or normalized.second or normalized.microsecond:
        raise ValueError("timestamp must align to an exact UTC hour")
    return normalized


def parse_hourly_timestamp(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError as error:
            raise ValueError("timestamp must be an ISO 8601 datetime with UTC offset") from error
    if not isinstance(value, datetime):
        # Pydantic validators must raise ValueError to report a typed validation issue.
        raise ValueError("timestamp must be an ISO 8601 datetime with UTC offset")  # noqa: TRY004
    return validate_hourly_timestamp(value)


class ActivityImportRequest(BaseModel):
    """JSON-native envelope for purchased-material or hourly electricity activity.

    ``content`` is CSV text for ``text/csv``. For ``application/json`` it may
    be the original JSON string, a list of row objects, a single row object, or
    an object containing a ``records`` list. Keeping parsing behind this
    envelope lets a multipart transport be added without changing the service.
    """

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    site_id: UUID
    reporting_period_id: UUID
    metric_definition_id: UUID
    source_name: str = Field(min_length=1, max_length=160)
    filename: str = Field(min_length=1, max_length=255)
    content_type: Literal["text/csv", "application/json"]
    content: ImportContent
    checksum: Sha256 | None = None
    external_reference: str | None = Field(default=None, max_length=255)
    is_synthetic: bool = False
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    interval_start: datetime | None = None
    interval_end: datetime | None = None

    @field_validator("interval_start", "interval_end", mode="before")
    @classmethod
    def validate_interval_boundary(cls, value: Any) -> datetime | None:
        return parse_hourly_timestamp(value) if value is not None else None

    @model_validator(mode="after")
    def validate_interval(self) -> ActivityImportRequest:
        if (self.interval_start is None) != (self.interval_end is None):
            raise ValueError("interval_start and interval_end must be supplied together")
        if self.interval_start is not None and self.interval_end is not None:
            if self.interval_end <= self.interval_start:
                raise ValueError("interval_end must be after interval_start (exclusive end)")
            if (self.interval_end - self.interval_start).total_seconds() > 366 * 86400:
                raise ValueError("An hourly import interval cannot exceed 366 days")
        return self

    @field_validator("source_name", "filename", mode="before")
    @classmethod
    def strip_required_labels(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("external_reference", mode="before")
    @classmethod
    def strip_optional_reference(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class SupplierImportRequest(BaseModel):
    """JSON-native envelope for supplier products and their evidence."""

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    source_name: str = Field(min_length=1, max_length=160)
    filename: str = Field(min_length=1, max_length=255)
    content_type: Literal["text/csv", "application/json"]
    content: ImportContent
    checksum: Sha256 | None = None
    external_reference: str | None = Field(default=None, max_length=255)
    is_synthetic: bool = False
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("source_name", "filename", mode="before")
    @classmethod
    def strip_required_labels(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("external_reference", mode="before")
    @classmethod
    def strip_optional_reference(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class ActivityRow(BaseModel):
    """Validated source row before deterministic unit normalization."""

    model_config = ConfigDict(extra="allow")

    row_key: str | None = Field(default=None, max_length=255)
    material_code: str = Field(min_length=1, max_length=100)
    activity_date: date | None = None
    quantity: ActivityQuantity
    unit: str = Field(min_length=1, max_length=50)
    unit_cost: Money | None = None
    currency: str | None = None
    supplier_product_id: UUID | None = None
    supplier_code: str | None = Field(default=None, max_length=100)
    supplier_product_code: str | None = Field(
        default=None,
        max_length=100,
        validation_alias=AliasChoices("supplier_product_code", "product_code"),
    )
    site_id: UUID | None = None
    reporting_period_id: UUID | None = None
    metric_definition_id: UUID | None = None

    @field_validator(
        "material_code",
        "unit",
        mode="before",
    )
    @classmethod
    def strip_required_row_labels(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator(
        "row_key",
        "activity_date",
        "unit_cost",
        "currency",
        "supplier_product_id",
        "supplier_code",
        "supplier_product_code",
        "site_id",
        "reporting_period_id",
        "metric_definition_id",
        mode="before",
    )
    @classmethod
    def empty_optional_activity_values_are_none(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
            raise ValueError("currency must be a three-letter ISO code")
        return normalized

    @model_validator(mode="after")
    def validate_reference_pairs(self) -> ActivityRow:
        if (self.unit_cost is None) != (self.currency is None):
            raise ValueError("unit_cost and currency must be supplied together")
        if (self.supplier_code is None) != (self.supplier_product_code is None):
            raise ValueError("supplier_code and supplier_product_code must be supplied together")
        if self.supplier_product_id is not None and self.supplier_code is not None:
            raise ValueError("use supplier_product_id or supplier/product codes, not both")
        return self


class HourlyElectricityRow(ActivityRow):
    """One hourly kWh interval; the source payload remains unchanged in storage."""

    material_code: Literal["ELECTRICITY"] = "ELECTRICITY"
    timestamp: datetime = Field(
        validation_alias=AliasChoices("timestamp", "interval_start", "activity_timestamp")
    )
    quantity: ActivityQuantity = Field(validation_alias=AliasChoices("quantity", "kwh"))
    unit: str = Field(default="kWh", min_length=1, max_length=50)
    site_code: str | None = Field(default=None, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def reject_ambiguous_aliases(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for aliases in (
                ("timestamp", "interval_start", "activity_timestamp"),
                ("quantity", "kwh"),
            ):
                if sum(key in value for key in aliases) > 1:
                    raise ValueError(f"Supply exactly one of {', '.join(aliases)}")
            if "kwh" in value and str(value.get("unit", "kWh")).strip().casefold() != "kwh":
                raise ValueError("The kwh field requires unit kWh; use quantity for other units")
        return value

    @field_validator("timestamp", mode="before")
    @classmethod
    def validate_timestamp(cls, value: Any) -> datetime:
        return parse_hourly_timestamp(value)

    @model_validator(mode="after")
    def validate_hourly_context(self) -> HourlyElectricityRow:
        if self.supplier_product_id is not None or self.supplier_code is not None:
            raise ValueError("Hourly electricity cannot reference a supplier product")
        if self.activity_date is not None and self.activity_date != self.timestamp.date():
            raise ValueError("activity_date must match the timestamp's UTC date")
        self.activity_date = self.timestamp.date()
        return self


class SupplierProductRow(BaseModel):
    """Validated supplier/product source row."""

    model_config = ConfigDict(extra="allow")

    row_key: str | None = Field(default=None, max_length=255)
    supplier_code: str = Field(min_length=1, max_length=100)
    supplier_name: str = Field(min_length=1, max_length=255)
    supplier_country_code: str = Field(
        min_length=2,
        max_length=2,
        validation_alias=AliasChoices("supplier_country_code", "country_code"),
    )
    product_code: str = Field(min_length=1, max_length=100)
    product_name: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("product_name", "name"),
    )
    material_code: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=100)
    description: str | None = None
    pcf_kgco2e_per_unit: ProductCarbonFootprint
    pcf_unit: str = Field(default="kgCO2e/kg", min_length=1, max_length=100)
    circularity_score: Score
    recycled_content_pct: Score
    recyclable_pct: Score
    evidence_quality_score: Score
    lead_time_days: int = Field(ge=0, le=2_147_483_647)
    unit_cost: Money
    currency: str
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True
    evidence_text: str = Field(min_length=1)
    evidence_type: str = Field(default="supplier_declaration", min_length=1, max_length=40)
    evidence_locator: str | None = Field(default=None, max_length=500)
    evidence_checksum: Sha256 | None = None
    supplier_metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "supplier_code",
        "supplier_name",
        "product_code",
        "product_name",
        "material_code",
        "category",
        "pcf_unit",
        "evidence_type",
        mode="before",
    )
    @classmethod
    def strip_product_labels(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator(
        "row_key",
        "description",
        "effective_to",
        "evidence_locator",
        "evidence_checksum",
        mode="before",
    )
    @classmethod
    def empty_optional_product_values_are_none(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("evidence_text")
    @classmethod
    def require_substantive_evidence(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence_text cannot be blank")
        return value

    @field_validator("supplier_country_code", mode="before")
    @classmethod
    def normalize_country(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().upper()
        if not normalized.isascii() or not normalized.isalpha():
            raise ValueError("supplier_country_code must be an ISO alpha-2 code")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
            raise ValueError("currency must be a three-letter ISO code")
        return normalized

    @model_validator(mode="after")
    def validate_effective_dates(self) -> SupplierProductRow:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be before effective_from")
        return self


class DataQualityIssueRead(BaseModel):
    id: UUID
    company_id: UUID
    raw_activity_record_id: UUID | None = None
    activity_record_id: UUID | None = None
    import_id: UUID | None = None
    row_number: int | None = None
    issue_type: str
    code: str
    severity: Literal["info", "warning", "error"]
    field_name: str | None = None
    message: str
    status: Literal["open", "resolved", "waived"]
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ImportResult(BaseModel):
    """Import run result; ``import_id`` is the persisted ``DataSource.id``."""

    import_id: UUID
    data_source_id: UUID
    source_document_id: UUID | None = None
    import_type: Literal["activity", "suppliers"]
    status: Literal["processing", "completed", "completed_with_errors", "failed"]
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    returned_issue_count: int = Field(ge=0)
    issues_truncated: bool
    is_synthetic: bool
    issues: list[DataQualityIssueRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DataQualityIssueList(BaseModel):
    items: list[DataQualityIssueRead]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class DataQualityIssueUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    actor_id: UUID
    status: Literal["resolved", "waived"]
    decision_note: str = Field(min_length=1, max_length=2000)

    @field_validator("decision_note", mode="before")
    @classmethod
    def strip_note(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
