from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GridZoneResolution = Literal[
    "request",
    "cached",
    "configured",
    "country_exact",
    "country_unique",
]


class ElectricityMapsZoneAccess(BaseModel):
    zone: str
    zone_name: str
    country_code: str | None = None
    accessible_endpoints: list[str] = Field(default_factory=list)


class ElectricityMapsTestResult(BaseModel):
    provider: str = "electricity_maps"
    api_version: str = "v4"
    authenticated: bool
    accessible_zone_count: int
    zones: list[ElectricityMapsZoneAccess]
    zones_truncated: bool


class GridIntensitySyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone: str | None = Field(default=None, min_length=2, max_length=100)
    start: datetime | None = None
    end: datetime | None = None
    lookback_hours: int = Field(default=24, ge=1, le=240)
    disable_estimations: bool = False

    @field_validator("zone")
    @classmethod
    def normalize_zone(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None

    @field_validator("start", "end")
    @classmethod
    def require_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Datetime values must include a UTC offset.")
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def validate_range_pair(self) -> GridIntensitySyncRequest:
        if (self.start is None) != (self.end is None):
            raise ValueError("start and end must be provided together")
        if self.start is not None and self.end is not None:
            if self.end <= self.start:
                raise ValueError("end must be later than start")
            if (self.end - self.start).total_seconds() > 240 * 3600:
                raise ValueError("The hourly sync range cannot exceed 240 hours.")
        return self


class GridIntensitySyncResult(BaseModel):
    site_id: UUID
    zone: str
    zone_resolution: GridZoneResolution
    requested_start: datetime
    requested_end: datetime
    received_points: int
    inserted_factors: int
    existing_factors: int
    estimated_points: int
    data_source_id: UUID
    source_document_id: UUID
    response_checksum: str


class GridIntensityProvenance(BaseModel):
    provider: str = "electricity_maps"
    api_version: str = "v4"
    cache_scope: Literal["company_zone"] = "company_zone"
    endpoint: str
    source_site_id: UUID | None = None
    source_document_id: UUID
    evidence_item_id: UUID
    response_checksum: str
    retrieved_at: datetime


class LatestGridIntensityResult(BaseModel):
    site_id: UUID
    factor_id: UUID
    zone: str
    value: Decimal
    unit: str
    provider_value_gco2eq_per_kwh: Decimal
    provider_timestamp: datetime
    provider_updated_at: datetime | None = None
    is_estimated: bool
    emission_factor_type: str
    temporal_granularity: str
    provenance: GridIntensityProvenance


class ElectricityMapsIntensityPoint(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    zone: str | None = None
    carbon_intensity: Decimal = Field(alias="carbonIntensity", ge=0)
    datetime: datetime
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    emission_factor_type: str = Field(default="lifecycle", alias="emissionFactorType")
    flow_traced: bool = Field(default=True, alias="flowTraced")
    is_estimated: bool = Field(default=False, alias="isEstimated")
    estimation_method: str | None = Field(default=None, alias="estimationMethod")
    temporal_granularity: str = Field(default="hourly", alias="temporalGranularity")

    @field_validator("datetime", "updated_at", "created_at")
    @classmethod
    def normalize_provider_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Provider datetime must include a UTC offset.")
        return value.astimezone(UTC)


class ElectricityMapsRangePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    zone: str
    data: list[ElectricityMapsIntensityPoint]
    temporal_granularity: str = Field(default="hourly", alias="temporalGranularity")
    aggregation_period: str | None = Field(default=None, alias="aggregationPeriod")


class SnapshotEnvelope(BaseModel):
    site_id: UUID
    requested_start: datetime
    requested_end: datetime
    response: dict[str, Any]
