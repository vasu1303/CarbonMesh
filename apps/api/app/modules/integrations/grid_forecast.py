"""Typed fixture/live boundary for immutable grid forecast inputs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import ValidationError

from app.modules.integrations.electricity_maps import (
    ElectricityMapsProvider,
    ElectricityMapsProviderError,
)
from app.modules.integrations.schemas import (
    ElectricityMapsForecastPayload,
    NormalizedGridForecast,
    NormalizedGridForecastPoint,
)

ForecastHorizon = Literal[6, 24, 48, 72]


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def normalize_electricity_maps_forecast(
    raw_payload: Mapping[str, Any],
    *,
    expected_zone: str,
    horizon_hours: ForecastHorizon,
    retrieved_at: datetime | None = None,
) -> NormalizedGridForecast:
    """Validate one provider snapshot and expose only deterministic typed points."""

    try:
        payload = ElectricityMapsForecastPayload.model_validate(dict(raw_payload))
    except ValidationError as error:
        raise ElectricityMapsProviderError(
            "Electricity Maps returned a forecast with an invalid shape.",
            code="integration_invalid_response",
            retryable=False,
        ) from error

    zone = expected_zone.strip().upper()
    if payload.zone != zone:
        raise ElectricityMapsProviderError(
            "Electricity Maps returned a forecast for a different zone.",
            code="integration_invalid_response",
            retryable=False,
        )
    if payload.temporal_granularity != "hourly":
        raise ElectricityMapsProviderError(
            "Electricity Maps returned a non-hourly forecast.",
            code="integration_invalid_response",
            retryable=False,
        )
    if len(payload.forecast) != horizon_hours:
        raise ElectricityMapsProviderError(
            "Electricity Maps returned an incomplete forecast horizon.",
            code="integration_incomplete_forecast",
            retryable=False,
        )

    ordered = sorted(payload.forecast, key=lambda item: item.datetime)
    timestamps = [item.datetime for item in ordered]
    if any(
        timestamp.minute != 0
        or timestamp.second != 0
        or timestamp.microsecond != 0
        for timestamp in timestamps
    ):
        raise ElectricityMapsProviderError(
            "Electricity Maps returned a forecast point that is not aligned to an exact UTC hour.",
            code="integration_invalid_response",
            retryable=False,
        )
    if len(set(timestamps)) != len(timestamps):
        raise ElectricityMapsProviderError(
            "Electricity Maps returned duplicate forecast intervals.",
            code="integration_invalid_response",
            retryable=False,
        )
    if any(
        right - left != timedelta(hours=1)
        for left, right in pairwise(timestamps)
    ):
        raise ElectricityMapsProviderError(
            "Electricity Maps returned a forecast with missing hourly intervals.",
            code="integration_incomplete_forecast",
            retryable=False,
        )
    if any(
        item.emission_factor_type != "lifecycle" or not item.flow_traced
        for item in ordered
    ):
        raise ElectricityMapsProviderError(
            "Electricity Maps returned a forecast for an unexpected factor method.",
            code="integration_invalid_response",
            retryable=False,
        )

    source_snapshot = dict(raw_payload)
    return NormalizedGridForecast(
        zone=zone,
        horizon_hours=horizon_hours,
        issued_at=payload.updated_at,
        retrieved_at=(retrieved_at or datetime.now(UTC)).astimezone(UTC),
        points=[
            NormalizedGridForecastPoint(
                forecast_for=item.datetime,
                intensity_gco2e_per_kwh=item.carbon_intensity,
                is_estimated=item.is_estimated,
                estimation_method=item.estimation_method,
                emission_factor_type=item.emission_factor_type,
                flow_traced=item.flow_traced,
            )
            for item in ordered
        ],
        response_checksum=hashlib.sha256(_json_bytes(source_snapshot)).hexdigest(),
        source_snapshot=source_snapshot,
    )


async def fetch_normalized_grid_forecast(
    provider: ElectricityMapsProvider,
    *,
    zone: str,
    horizon_hours: ForecastHorizon = 24,
    retrieved_at: datetime | None = None,
) -> NormalizedGridForecast:
    raw_payload = await provider.get_carbon_intensity_forecast(
        zone=zone,
        horizon_hours=horizon_hours,
        disable_estimations=False,
    )
    return normalize_electricity_maps_forecast(
        raw_payload,
        expected_zone=zone,
        horizon_hours=horizon_hours,
        retrieved_at=retrieved_at,
    )


class ElectricityMapsFixtureClient:
    """Credential-free adapter over checked-in Electricity Maps-shaped fixtures."""

    is_synthetic: ClassVar[bool] = True

    def __init__(self, fixture_directory: Path) -> None:
        self._fixture_directory = fixture_directory

    async def list_zones(self) -> Mapping[str, Any]:
        return {
            "IN": {
                "zoneKey": "IN",
                "displayName": "India (synthetic fixture)",
                "countryCode": "IN",
                "access": ["carbon-intensity/past-range", "carbon-intensity/forecast"],
            }
        }

    async def get_carbon_intensity_range(
        self,
        *,
        zone: str,
        start: datetime,
        end: datetime,
        disable_estimations: bool = False,
    ) -> Mapping[str, Any]:
        payload = self._read_json("grid-history.json")
        if str(payload.get("zone", "")).upper() != zone.upper():
            raise ElectricityMapsProviderError(
                "The grid history fixture does not contain the requested zone.",
                code="fixture_zone_not_found",
                retryable=False,
            )
        points = []
        for point in payload.get("data", []):
            timestamp = datetime.fromisoformat(str(point["datetime"]))
            if start <= timestamp < end and not (
                disable_estimations and bool(point.get("isEstimated", False))
            ):
                points.append(point)
        return {**payload, "data": points}

    async def get_carbon_intensity_forecast(
        self,
        *,
        zone: str,
        horizon_hours: int = 24,
        disable_estimations: bool = False,
    ) -> Mapping[str, Any]:
        if horizon_hours not in {6, 24, 48, 72}:
            raise ElectricityMapsProviderError(
                "Forecast fixture horizon must be 6, 24, 48, or 72 hours.",
                code="integration_invalid_forecast_horizon",
                retryable=False,
            )
        if disable_estimations:
            raise ElectricityMapsProviderError(
                "Forecast fixtures require estimated values.",
                code="integration_forecast_estimations_required",
                retryable=False,
            )
        payload = self._read_json(f"grid-forecast-{horizon_hours}h.json")
        if str(payload.get("zone", "")).upper() != zone.upper():
            raise ElectricityMapsProviderError(
                "The forecast fixture does not contain the requested zone.",
                code="fixture_zone_not_found",
                retryable=False,
            )
        return payload

    def _read_json(self, filename: str) -> dict[str, Any]:
        try:
            payload = json.loads((self._fixture_directory / filename).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ElectricityMapsProviderError(
                "The requested synthetic grid fixture is unavailable.",
                code="fixture_unavailable",
                retryable=False,
            ) from error
        if not isinstance(payload, dict):
            raise ElectricityMapsProviderError(
                "The requested synthetic grid fixture has an invalid shape.",
                code="fixture_invalid",
                retryable=False,
            )
        return payload
