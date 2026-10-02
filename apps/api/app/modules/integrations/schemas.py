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


GridFixtureVariant = Literal["quality_cases_v1", "complete_q3_v1"]


class GridIntensitySyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["fixture", "live"] = "live"
    fixture_variant: GridFixtureVariant = "quality_cases_v1"
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
        if self.mode != "fixture" and self.fixture_variant != "quality_cases_v1":
            raise ValueError("A fixture variant can only be selected in explicit fixture mode.")
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
    inserted_points: int
    existing_points: int
    estimated_points: int
    data_source_id: UUID
    source_document_id: UUID
    response_checksum: str


class GridIntensityProvenance(BaseModel):
    provider: str = "electricity_maps"
    api_version: str = "v4"
    provider_mode: Literal["fixture", "live"]
    synthetic: bool
    cache_scope: Literal["site_zone"] = "site_zone"
    endpoint: str
    source_site_id: UUID | None = None
    source_document_id: UUID
    evidence_item_id: UUID
    response_checksum: str
    retrieved_at: datetime


class LatestGridIntensityResult(BaseModel):
    site_id: UUID
    grid_intensity_point_id: UUID
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

    @field_validator("datetime")
    @classmethod
    def normalize_provider_point_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Provider datetime must include a UTC offset.")
        normalized = value.astimezone(UTC)
        if any((normalized.minute, normalized.second, normalized.microsecond)):
            raise ValueError("Hourly provider points must align to an exact UTC hour.")
        return normalized

    @field_validator("updated_at", "created_at")
    @classmethod
    def normalize_provider_metadata_datetime(cls, value: datetime | None) -> datetime | None:
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


class ElectricityMapsForecastPoint(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    carbon_intensity: Decimal = Field(alias="carbonIntensity", ge=0)
    datetime: datetime
    is_estimated: bool = Field(default=True, alias="isEstimated")
    estimation_method: str | None = Field(
        default="electricity_maps_forecast",
        alias="estimationMethod",
    )
    emission_factor_type: str = Field(default="lifecycle", alias="emissionFactorType")
    flow_traced: bool = Field(default=True, alias="flowTraced")

    @field_validator("datetime")
    @classmethod
    def normalize_forecast_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Forecast datetime must include a UTC offset.")
        return value.astimezone(UTC)


class ElectricityMapsForecastPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    zone: str
    forecast: list[ElectricityMapsForecastPoint]
    updated_at: datetime = Field(alias="updatedAt")
    temporal_granularity: str = Field(default="hourly", alias="temporalGranularity")

    @field_validator("zone")
    @classmethod
    def normalize_forecast_zone(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("updated_at")
    @classmethod
    def normalize_forecast_issued_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Forecast updatedAt must include a UTC offset.")
        return value.astimezone(UTC)


class NormalizedGridForecastPoint(BaseModel):
    forecast_for: datetime
    intensity_gco2e_per_kwh: Decimal
    is_estimated: bool
    estimation_method: str | None = None
    emission_factor_type: str
    flow_traced: bool


class NormalizedGridForecast(BaseModel):
    provider: Literal["electricity_maps"] = "electricity_maps"
    api_version: Literal["v4"] = "v4"
    endpoint: Literal["/carbon-intensity/forecast"] = "/carbon-intensity/forecast"
    zone: str
    horizon_hours: Literal[6, 24, 48, 72]
    issued_at: datetime
    retrieved_at: datetime
    temporal_granularity: Literal["hourly"] = "hourly"
    points: list[NormalizedGridForecastPoint]
    response_checksum: str
    source_snapshot: dict[str, Any]


class SnapshotEnvelope(BaseModel):
    site_id: UUID
    requested_start: datetime
    requested_end: datetime
    provider_mode: Literal["fixture", "live"]
    synthetic: bool
    response: dict[str, Any]
