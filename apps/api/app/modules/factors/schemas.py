from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FactorValue = Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=12, allow_inf_nan=False)]
QualityValue = Annotated[
    Decimal, Field(ge=0, le=1, max_digits=6, decimal_places=5, allow_inf_nan=False)
]


class FactorFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_id: UUID
    metric_definition_id: UUID
    evidence_item_id: UUID
    factor_code: Annotated[str, Field(min_length=1, max_length=100)]
    version: Annotated[str, Field(min_length=1, max_length=50)]
    name: Annotated[str, Field(min_length=1, max_length=255)]
    material_code: Annotated[str, Field(min_length=1, max_length=100)]
    product_code: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    geography: Annotated[str, Field(min_length=2, max_length=100)] = "GLOBAL"
    factor_value: FactorValue
    numerator_unit: Literal["kgCO2e"] = "kgCO2e"
    denominator_unit: Literal["kg"] = "kg"
    effective_from: date
    effective_to: date | None = None
    source_quality: QualityValue
    factor_specificity: QualityValue
    factor_recency: QualityValue

    @field_validator("factor_value", "source_quality", "factor_specificity", "factor_recency")
    @classmethod
    def canonical_decimal(cls, value: Decimal) -> Decimal:
        return value.normalize() if value else Decimal(0)

    @model_validator(mode="after")
    def valid_period(self):
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        return self


class FactorRegisterRequest(FactorFields):
    actor_id: UUID


class FactorView(FactorFields):
    # Legacy and grid factors may have broader units and no material scope.
    material_code: str | None
    numerator_unit: str
    denominator_unit: str
    id: UUID
    status: Literal["active", "inactive", "superseded"]
    created_at: datetime


class FactorRegisterResponse(BaseModel):
    factor: FactorView
    ledger_event_id: UUID
    replayed: bool


class FactorListResponse(BaseModel):
    items: list[FactorView]
    total: int
    limit: int
    offset: int
