from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

ContextWorkflow = Literal[
    "measurement",
    "procurement",
    "measurement_procurement",
    "cross_module",
]


class MetricDefinitionRead(BaseModel):
    """Public, versioned semantic metric contract."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    key: str
    version: str
    name: str
    canonical_unit: str
    dimensions: dict[str, Any]
    handler: str
    method_version: str
    description: str | None = None


class MetricDefinitionList(BaseModel):
    model_config = ConfigDict(frozen=True)

    company_id: UUID
    items: list[MetricDefinitionRead]
    count: int = Field(ge=0)


class ContextConstraints(BaseModel):
    """Hard procurement constraints frozen into the context signature."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_cost_increase_pct: Decimal | None = Field(default=None, ge=0, le=100)
    max_lead_time_days: int | None = Field(default=None, ge=0)
    minimum_circularity_score: Decimal | None = Field(default=None, ge=0, le=100)


class ContextResolveRequest(BaseModel):
    """Exact identifiers and constraints to resolve into a frozen context."""

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    site_id: UUID
    reporting_period_id: UUID
    metric_definition_ids: list[UUID] = Field(min_length=1, max_length=16)
    workflow: ContextWorkflow
    actor_id: UUID | None = None
    supplier_scope: list[UUID] = Field(default_factory=list, max_length=100)
    material_scope: list[str] = Field(default_factory=list, max_length=100)
    constraints: ContextConstraints = Field(default_factory=ContextConstraints)

    @field_validator("metric_definition_ids", "supplier_scope")
    @classmethod
    def identifiers_must_be_unique(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Identifiers must be unique.")
        return value

    @field_validator("material_scope")
    @classmethod
    def normalize_material_scope(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("Material scope values must not be blank.")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Material scope values must be unique.")
        return normalized


class ResolvedCompany(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    code: str
    name: str
    is_synthetic: bool


class ResolvedSite(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    code: str
    name: str
    country_code: str
    timezone: str


class ResolvedReportingPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    start_date: date
    end_date: date
    status: str


class ResolvedActor(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    display_name: str
    role: str


class ResolvedSupplier(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    supplier_code: str
    name: str


class ContextEnvelope(BaseModel):
    """Resolved context whose full contents are bound by ``analysis_signature``."""

    model_config = ConfigDict(frozen=True)

    company: ResolvedCompany
    site: ResolvedSite
    reporting_period: ResolvedReportingPeriod
    metrics: list[MetricDefinitionRead]
    workflow: ContextWorkflow
    actor: ResolvedActor | None
    supplier_scope: list[ResolvedSupplier]
    material_scope: list[str]
    constraints: ContextConstraints
    analysis_signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class APIErrorDetail(BaseModel):
    code: str
    message: str
    trace_id: str
    retryable: bool = False
    field_details: dict[str, str] = Field(default_factory=dict)


class APIErrorResponse(BaseModel):
    detail: APIErrorDetail
