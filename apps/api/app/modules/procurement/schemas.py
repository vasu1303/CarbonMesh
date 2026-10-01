from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
ConstraintValue = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]


class ProcurementSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class EvidenceSummary(ProcurementSchema):
    id: UUID
    evidence_type: str
    locator: str
    checksum: str
    metadata: dict[str, Any]


class EvidenceDetail(EvidenceSummary):
    source_document_id: UUID
    content_text: str
    created_at: datetime
    updated_at: datetime


class SupplierProductSummary(ProcurementSchema):
    id: UUID
    supplier_id: UUID
    supplier_code: str
    supplier_name: str
    supplier_country_code: str
    supplier_status: str
    risk: str
    product_code: str
    name: str
    material_code: str
    category: str
    description: str | None
    pcf_kgco2e_per_unit: Decimal
    pcf_unit: str
    circularity_score: Decimal
    recycled_content_pct: Decimal
    recyclable_pct: Decimal
    evidence_quality_score: Decimal
    lead_time_days: int
    unit_cost: Decimal
    currency: CurrencyCode
    effective_from: date
    effective_to: date | None
    is_active: bool
    evidence_item_id: UUID | None
    evidence_available: bool


class SupplierProductDetail(SupplierProductSummary):
    company_id: UUID
    supplier_metadata: dict[str, Any]
    evidence: EvidenceDetail | None
    created_at: datetime
    updated_at: datetime


class SupplierProductList(ProcurementSchema):
    items: list[SupplierProductSummary]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ScoringWeights(ProcurementSchema):
    carbon: Decimal
    evidence: Decimal
    circularity: Decimal
    operational_fit: Decimal


class MaterialConstraints(ProcurementSchema):
    """The complete allowlist of supported material feasibility constraints."""

    allowed_material_codes: list[ConstraintValue] = Field(default_factory=list, max_length=100)
    excluded_risk_levels: list[ConstraintValue] = Field(default_factory=list, max_length=100)

    @field_validator("allowed_material_codes")
    @classmethod
    def unique_material_codes(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("allowed_material_codes must not contain duplicates")
        return values

    @field_validator("excluded_risk_levels")
    @classmethod
    def normalized_unique_risk_levels(cls, values: list[str]) -> list[str]:
        normalized = [value.lower() for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("excluded_risk_levels must not contain duplicates")
        return normalized


class ScenarioConstraints(ProcurementSchema):
    max_cost_increase_pct: Decimal
    max_lead_time_days: int
    minimum_circularity_score: Decimal
    material: MaterialConstraints


class ScoreComponents(ProcurementSchema):
    carbon: Decimal
    evidence: Decimal
    circularity: Decimal
    operational_fit: Decimal
    total: Decimal


class InfeasibilityReason(ProcurementSchema):
    code: str
    message: str
    actual: str | int | bool | None = None
    required: str | int | bool | None = None


class ProductImpact(ProcurementSchema):
    projected_footprint_kgco2e: Decimal
    avoided_kgco2e: Decimal
    reduction_pct: Decimal
    cost_delta_pct: Decimal
    lead_time_delta_days: int


class ProductAssessment(ProcurementSchema):
    score_id: UUID
    product: SupplierProductSummary
    scores: ScoreComponents
    feasible: bool
    infeasibility_reasons: list[InfeasibilityReason]
    rank: int | None
    impact: ProductImpact


class ScoringMethod(ProcurementSchema):
    id: UUID
    key: str
    version: str
    code_version: str
    weights: ScoringWeights


class AssessmentRunRequest(ProcurementSchema):
    company_id: UUID
    scenario_id: UUID


class ScoreScenarioRequest(ProcurementSchema):
    """Tenant scope for a scenario identified authoritatively by the URL path."""

    company_id: UUID


class AssessmentRunResult(ProcurementSchema):
    scenario_id: UUID
    method: ScoringMethod
    terminal_state: Literal["completed", "no_feasible_option"]
    assessments: list[ProductAssessment]
    selected_product_id: UUID | None
    recommendation_id: UUID | None


class CreateScenarioRequest(ProcurementSchema):
    company_id: UUID
    site_id: UUID
    reporting_period_id: UUID
    current_product_id: UUID
    carbon_measurement_id: UUID
    method_definition_id: UUID
    requested_by: UUID
    agent_run_id: UUID | None = None
    quantity: Decimal = Field(gt=0)
    quantity_unit: str = Field(min_length=1, max_length=50)
    current_unit_cost: Decimal | None = None
    currency: CurrencyCode | None = None
    max_cost_increase_pct: Decimal = Field(default=Decimal(5), ge=0, le=100)
    max_lead_time_days: int = Field(default=30, ge=0)
    minimum_circularity_score: Decimal = Field(default=Decimal(0), ge=0, le=100)
    material_constraints: MaterialConstraints = Field(default_factory=MaterialConstraints)
    approval_expires_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)


class ApprovalPreviewSummary(ProcurementSchema):
    id: UUID
    status: str
    preview_hash: str
    analysis_signature: str
    expires_at: datetime


class RecommendationSummary(ProcurementSchema):
    id: UUID
    status: str
    recommended_product_id: UUID
    payload_hash: str
    analysis_signature: str
    impact: ProductImpact
    approval: ApprovalPreviewSummary | None = None


class ProcurementScenarioResult(ProcurementSchema):
    id: UUID
    company_id: UUID
    site_id: UUID
    reporting_period_id: UUID
    current_product: SupplierProductSummary
    carbon_measurement_id: UUID
    agent_run_id: UUID | None
    method: ScoringMethod
    quantity: Decimal
    quantity_unit: str
    current_unit_cost: Decimal
    currency: CurrencyCode
    constraints: ScenarioConstraints
    weights: ScoringWeights
    analysis_signature: str
    frozen_context: dict[str, Any]
    status: str
    terminal_state: Literal["completed", "no_feasible_option"]
    alternatives: list[ProductAssessment]
    selected_recommendation: RecommendationSummary | None
    created_at: datetime
    updated_at: datetime


class FactBindingView(ProcurementSchema):
    id: UUID | None = None
    placeholder: str
    value: Any
    display_value: str
    unit: str | None
    ledger_event_id: UUID | None = None
    evidence_item_id: UUID | None = None


class BoundNarrative(ProcurementSchema):
    template_id: str
    template: str
    resolved_text: str
    fact_bindings: list[FactBindingView]
    evidence_links: list[UUID]
    unsupported_fragments: list[str]


class RecommendationDetail(ProcurementSchema):
    id: UUID
    company_id: UUID
    scenario_id: UUID
    status: str
    baseline_product: SupplierProductSummary
    recommended_product: SupplierProductSummary
    supplier_score: ProductAssessment
    evidence: list[EvidenceSummary]
    projected_footprint_kgco2e: Decimal
    avoided_kgco2e: Decimal
    reduction_pct: Decimal
    cost_delta_pct: Decimal
    lead_time_delta_days: int
    narrative: BoundNarrative
    analysis_signature: str
    payload_hash: str
    impact_snapshot: dict[str, Any]
    ledger_event_id: UUID | None
    approval: ApprovalPreviewSummary | None
    invalidated_at: datetime | None
    created_at: datetime
