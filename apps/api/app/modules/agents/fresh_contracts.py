"""Explicit source inputs for domain creation; never model-invented identifiers."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FreshMeasurementInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    material_code: str = Field(min_length=1, max_length=100)
    output_metric_key: Literal[
        "emissions.scope2.location_based", "emissions.scope3.category1"
    ]
    activity_record_ids: tuple[UUID, ...] = Field(default=(), max_length=3000)
    geography: str | None = Field(default=None, min_length=2, max_length=100)
    grid_zone: str | None = Field(default=None, min_length=1, max_length=100)
    grid_method_version: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("activity_record_ids")
    @classmethod
    def unique_activities(cls, values: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(values) != len(set(values)):
            raise ValueError("activity identifiers must be unique")
        return values


class FreshRunInputs(BaseModel):
    """Bounded declarative inputs; tenant/actor/site/period come from context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    measurements: tuple[FreshMeasurementInput, ...] = Field(default=(), max_length=2)
    history_start: datetime | None = None
    history_end: datetime | None = None
    history_fixture_variant: Literal["quality_cases_v1", "complete_q3_v1"] = "quality_cases_v1"
    procurement_quantity: Decimal | None = Field(default=None, gt=0)
    procurement_quantity_unit: Literal["kg"] = "kg"
    procurement_method_id: UUID | None = None
    dispatch_method_id: UUID | None = None
    dispatch_baseline_start: datetime | None = None

    @field_validator("history_start", "history_end", "dispatch_baseline_start")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("timestamps require a UTC offset")
        return value

    @model_validator(mode="after")
    def bounded_inputs(self) -> "FreshRunInputs":
        metrics = [item.output_metric_key for item in self.measurements]
        if len(metrics) != len(set(metrics)):
            raise ValueError("only one source calculation per output metric is allowed")
        if (self.history_start is None) != (self.history_end is None):
            raise ValueError("history_start and history_end must be supplied together")
        if self.history_start is not None and self.history_end is not None:
            seconds = (self.history_end - self.history_start).total_seconds()
            if seconds <= 0 or seconds > 93 * 86400:
                raise ValueError("history range must be positive and at most 93 days")
            if any(value.minute or value.second or value.microsecond for value in
                   (self.history_start, self.history_end)):
                raise ValueError("history boundaries must align to exact hours")
        return self
