from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MeasurementStatus = Literal["draft", "verified", "superseded", "unsupported"]
MeasurementTerminalState = Literal[
    "completed",
    "no_data",
    "needs_clarification",
    "unsupported",
    "validation_error",
    "failed_validation",
]
DecimalZeroToOne = Annotated[Decimal, Field(ge=0, le=1)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MeasurementCalculateRequest(StrictSchema):
    company_id: UUID
    site_id: UUID
    reporting_period_id: UUID
    material_code: Annotated[str, Field(min_length=1, max_length=100)]
    activity_metric_key: Annotated[str, Field(min_length=1, max_length=150)] = (
        "activity.purchased_material_mass"
    )
    output_metric_key: Annotated[str, Field(min_length=1, max_length=150)] = (
        "emissions.scope3.category1"
    )
    method_key: Annotated[str, Field(min_length=1, max_length=150)] = (
        "measurement.scope3.category1.mass_factor"
    )
    geography: Annotated[str | None, Field(min_length=2, max_length=100)] = None
    activity_record_ids: Annotated[list[UUID] | None, Field(min_length=1, max_length=500)] = None
    actor_id: UUID | None = None
    agent_run_id: UUID | None = None
    trace_id: Annotated[str | None, Field(min_length=1, max_length=100)] = None

    @field_validator(
        "material_code",
        "activity_metric_key",
        "output_metric_key",
        "method_key",
        "geography",
        "trace_id",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("activity_record_ids")
    @classmethod
    def reject_duplicate_activity_ids(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("activity_record_ids must not contain duplicates")
        return value


class MeasurementErrorDetail(StrictSchema):
    code: str
    message: str
    trace_id: str
    retryable: bool = False
    terminal_state: MeasurementTerminalState
    field_details: dict[str, object] = Field(default_factory=dict)


class MeasurementErrorEnvelope(StrictSchema):
    detail: MeasurementErrorDetail


class EvidenceReference(StrictSchema):
    id: UUID
    source_document_id: UUID
    data_source_id: UUID
    source_document_filename: str
    source_document_checksum: str
    evidence_type: str
    locator: str
    checksum: str


class MeasurementInput(StrictSchema):
    activity_record_id: UUID
    raw_activity_record_id: UUID
    data_source_id: UUID
    source_document_id: UUID
    source_row_key: str
    source_row_number: int | None
    raw_checksum: str
    supplier_product_id: UUID | None
    product_code: str | None
    material_code: str
    activity_date: date | None
    source_quantity: NonNegativeDecimal
    source_unit: str
    normalized_quantity_kg: NonNegativeDecimal
    record_completeness: DecimalZeroToOne


class EmissionFactorReference(StrictSchema):
    id: UUID
    factor_code: str
    version: str
    name: str
    material_code: str | None
    product_code: str | None
    geography: str
    factor_value: NonNegativeDecimal
    numerator_unit: str
    denominator_unit: str
    normalized_factor_kgco2e_per_kg: NonNegativeDecimal
    effective_from: date
    effective_to: date | None
    evidence: EvidenceReference


class EmissionCalculationDetail(StrictSchema):
    id: UUID
    activity_record_id: UUID
    emission_factor_id: UUID
    normalized_quantity_kg: NonNegativeDecimal
    factor_kgco2e_per_kg: NonNegativeDecimal
    emissions_kgco2e: NonNegativeDecimal
    formula: str
    output_hash: str


class ConfidenceBreakdownResponse(StrictSchema):
    source_quality: DecimalZeroToOne
    factor_specificity: DecimalZeroToOne
    factor_recency: DecimalZeroToOne
    record_completeness: DecimalZeroToOne
    source_quality_weight: DecimalZeroToOne
    factor_specificity_weight: DecimalZeroToOne
    factor_recency_weight: DecimalZeroToOne
    record_completeness_weight: DecimalZeroToOne
    overall: DecimalZeroToOne


class BaselineComparison(StrictSchema):
    baseline_id: UUID
    name: str
    value_kgco2e: NonNegativeDecimal
    unit: str
    variance_kgco2e: Decimal
    variance_pct: Decimal | None
    variance_alert_id: UUID | None = None


class CalculationRunReference(StrictSchema):
    id: UUID
    method_definition_id: UUID
    method_key: str
    method_version: str
    code_version: str
    rounding_policy: str
    input_hash: str
    output_hash: str
    status: Literal["pending", "running", "completed", "failed"]
    started_at: datetime | None
    completed_at: datetime | None


class MeasurementFactReference(StrictSchema):
    fact_id: UUID
    ledger_event_id: UUID | None
    output_hash: str
    audit_log_id: UUID | None


class MeasurementDetail(StrictSchema):
    id: UUID
    company_id: UUID
    site_id: UUID
    site_name: str
    reporting_period_id: UUID
    reporting_period_name: str
    metric_definition_id: UUID
    metric_key: str
    metric_version: str
    category: str
    value_kgco2e: NonNegativeDecimal
    unit: str
    confidence: DecimalZeroToOne
    confidence_breakdown: ConfidenceBreakdownResponse
    status: MeasurementStatus
    formula: str
    output_hash: str
    verified_at: datetime | None
    created_at: datetime
    calculation_run: CalculationRunReference
    inputs: list[MeasurementInput]
    factors: list[EmissionFactorReference]
    calculations: list[EmissionCalculationDetail]
    baseline: BaselineComparison | None
    facts: MeasurementFactReference


class MeasurementResult(MeasurementDetail):
    terminal_state: Literal["completed"] = "completed"
    trace_id: str
    idempotent: bool = False


class MeasurementSummary(StrictSchema):
    id: UUID
    company_id: UUID
    site_id: UUID
    site_name: str
    reporting_period_id: UUID
    reporting_period_name: str
    metric_definition_id: UUID
    metric_key: str
    metric_version: str
    category: str
    value_kgco2e: NonNegativeDecimal
    unit: str
    confidence: DecimalZeroToOne
    status: MeasurementStatus
    ledger_event_id: UUID | None
    output_hash: str
    verified_at: datetime | None
    created_at: datetime


class MeasurementListResponse(StrictSchema):
    items: list[MeasurementSummary]
    total: Annotated[int, Field(ge=0)]
    limit: Annotated[int, Field(ge=1, le=100)]
    offset: Annotated[int, Field(ge=0)]
