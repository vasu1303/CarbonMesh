from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr, ValidationError

from app.core.observability import begin_external_usage, take_external_usage
from app.modules.integrations.electricity_maps import (
    ElectricityMapsHttpClient,
    ElectricityMapsProviderError,
)
from app.modules.integrations.grid_forecast import (
    ElectricityMapsFixtureClient,
    ForecastHorizon,
    fetch_normalized_grid_forecast,
    normalize_electricity_maps_forecast,
)
from app.modules.integrations.schemas import ElectricityMapsRangePayload

DEMO_FIXTURES = Path(__file__).resolve().parents[4] / "data" / "demo"


class FakeResponse:
    def __init__(self, payload: dict[str, object] | bytes) -> None:
        self._raw = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int) -> bytes:
        return self._raw


def forecast_payload(hours: int = 6) -> dict[str, object]:
    start = datetime(2026, 10, 1, 8, tzinfo=UTC)
    return {
        "zone": "IN",
        "forecast": [
            {
                "carbonIntensity": 300 + index,
                "datetime": (start + timedelta(hours=index)).isoformat(),
            }
            for index in range(hours)
        ],
        "updatedAt": "2026-10-01T07:30:00Z",
        "temporalGranularity": "hourly",
    }


@pytest.mark.asyncio
async def test_live_forecast_adapter_uses_official_v4_path_and_query(monkeypatch) -> None:
    requests = []

    def fake_urlopen(request, *, timeout):
        requests.append((request, timeout))
        return FakeResponse(forecast_payload())

    monkeypatch.setattr(
        "app.modules.integrations.electricity_maps.urlopen",
        fake_urlopen,
    )
    client = ElectricityMapsHttpClient(
        token=SecretStr("credential-must-not-leak"),
        timeout_seconds=2,
    )

    payload = await client.get_carbon_intensity_forecast(zone="IN", horizon_hours=6)

    assert payload["zone"] == "IN"
    assert len(requests) == 1
    request, timeout = requests[0]
    parsed = urlparse(request.full_url)
    assert parsed.path == "/v4/carbon-intensity/forecast"
    assert parse_qs(parsed.query) == {
        "zone": ["IN"],
        "horizonHours": ["6"],
        "temporalGranularity": ["hourly"],
        "disableCallerLookup": ["true"],
    }
    assert "disableEstimations" not in parsed.query
    assert "credential-must-not-leak" not in request.full_url
    assert request.get_header("Auth-token") == "credential-must-not-leak"
    assert timeout == 2


@pytest.mark.asyncio
async def test_live_provider_retries_one_transient_failure(monkeypatch) -> None:
    attempts = 0
    begin_external_usage()

    def flaky_urlopen(request, *, timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HTTPError(request.full_url, 500, "provider failure", hdrs=None, fp=None)
        return FakeResponse(forecast_payload())

    monkeypatch.setattr(
        "app.modules.integrations.electricity_maps.urlopen",
        flaky_urlopen,
    )
    client = ElectricityMapsHttpClient(token=SecretStr("secret"), timeout_seconds=2)

    await client.get_carbon_intensity_forecast(zone="IN", horizon_hours=6)

    assert attempts == 2
    usage = take_external_usage()
    assert usage.api_calls == 2
    assert usage.retry_count == 1
    assert usage.cache_hits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "rate_limit", "server_error"])
async def test_live_provider_bounds_all_transient_failures_to_one_retry(
    monkeypatch,
    failure: str,
) -> None:
    attempts = 0

    def unavailable_urlopen(request, *, timeout):
        nonlocal attempts
        attempts += 1
        if failure == "timeout":
            raise TimeoutError
        status = 429 if failure == "rate_limit" else 503
        raise HTTPError(request.full_url, status, "provider failure", hdrs=None, fp=None)

    monkeypatch.setattr(
        "app.modules.integrations.electricity_maps.urlopen",
        unavailable_urlopen,
    )
    client = ElectricityMapsHttpClient(token=SecretStr("secret"), timeout_seconds=2)

    with pytest.raises(ElectricityMapsProviderError) as caught:
        await client.get_carbon_intensity_forecast(zone="IN", horizon_hours=6)

    assert attempts == 2
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_live_provider_does_not_retry_authentication_failure(monkeypatch) -> None:
    attempts = 0
    begin_external_usage()

    def rejected_urlopen(request, *, timeout):
        nonlocal attempts
        attempts += 1
        raise HTTPError(request.full_url, 401, "do not expose this", hdrs=None, fp=None)

    monkeypatch.setattr(
        "app.modules.integrations.electricity_maps.urlopen",
        rejected_urlopen,
    )
    client = ElectricityMapsHttpClient(token=SecretStr("top-secret"), timeout_seconds=2)

    with pytest.raises(ElectricityMapsProviderError) as caught:
        await client.get_carbon_intensity_forecast(zone="IN", horizon_hours=6)

    assert attempts == 1
    usage = take_external_usage()
    assert usage.api_calls == 1
    assert usage.retry_count == 0
    assert caught.value.code == "integration_authentication_failed"
    assert caught.value.retryable is False
    assert "top-secret" not in str(caught.value)
    assert "do not expose this" not in str(caught.value)


@pytest.mark.asyncio
async def test_live_provider_does_not_retry_invalid_json(monkeypatch) -> None:
    attempts = 0

    def invalid_urlopen(request, *, timeout):
        nonlocal attempts
        attempts += 1
        return FakeResponse(b"{not-json")

    monkeypatch.setattr(
        "app.modules.integrations.electricity_maps.urlopen",
        invalid_urlopen,
    )
    client = ElectricityMapsHttpClient(token=SecretStr("secret"), timeout_seconds=2)

    with pytest.raises(ElectricityMapsProviderError) as caught:
        await client.get_carbon_intensity_forecast(zone="IN", horizon_hours=6)

    assert attempts == 1
    assert caught.value.code == "integration_invalid_response"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_live_provider_does_not_retry_missing_configuration(monkeypatch) -> None:
    attempts = 0

    def unexpected_urlopen(request, *, timeout):
        nonlocal attempts
        attempts += 1
        return FakeResponse(forecast_payload())

    monkeypatch.setattr(
        "app.modules.integrations.electricity_maps.urlopen",
        unexpected_urlopen,
    )
    client = ElectricityMapsHttpClient(token=None, timeout_seconds=2)

    with pytest.raises(ElectricityMapsProviderError) as caught:
        await client.get_carbon_intensity_forecast(zone="IN", horizon_hours=6)

    assert attempts == 0
    assert caught.value.code == "integration_not_configured"
    assert caught.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize("horizon", [6, 24, 48, 72])
async def test_fixture_and_live_payloads_share_one_normalized_contract(horizon: int) -> None:
    provider = ElectricityMapsFixtureClient(DEMO_FIXTURES)

    normalized = await fetch_normalized_grid_forecast(
        provider,
        zone="IN",
        horizon_hours=cast(ForecastHorizon, horizon),
        retrieved_at=datetime(2026, 10, 1, 7, 35, tzinfo=UTC),
    )

    assert normalized.zone == "IN"
    assert normalized.horizon_hours == horizon
    assert len(normalized.points) == horizon
    assert all(point.is_estimated for point in normalized.points)
    assert normalized.points[0].forecast_for == datetime(2026, 10, 1, 8, tzinfo=UTC)
    assert len(normalized.response_checksum) == 64
    assert normalized.source_snapshot["synthetic"] is True


def test_forecast_contract_fails_closed_on_missing_interval() -> None:
    payload = forecast_payload()
    forecast = payload["forecast"]
    assert isinstance(forecast, list)
    forecast[3]["datetime"] = "2026-10-01T14:00:00Z"

    with pytest.raises(ElectricityMapsProviderError) as caught:
        normalize_electricity_maps_forecast(
            payload,
            expected_zone="IN",
            horizon_hours=6,
        )

    assert caught.value.code == "integration_incomplete_forecast"
    assert caught.value.retryable is False


@pytest.mark.parametrize("horizon", [6, 24, 48, 72])
def test_forecast_accepts_inclusive_endpoint_without_extending_horizon(horizon: int) -> None:
    payload = forecast_payload(horizon + 1)
    original = json.dumps(payload, sort_keys=True)

    normalized = normalize_electricity_maps_forecast(
        payload, expected_zone="IN", horizon_hours=cast(ForecastHorizon, horizon)
    )

    start = datetime(2026, 10, 1, 8, tzinfo=UTC)
    assert len(normalized.points) == horizon
    assert normalized.points[0].forecast_for == start
    assert normalized.points[-1].forecast_for + timedelta(hours=1) == (
        start + timedelta(hours=horizon)
    )
    assert len(normalized.source_snapshot["forecast"]) == horizon + 1
    assert json.dumps(payload, sort_keys=True) == original


@pytest.mark.asyncio
async def test_live_forecast_normalizes_25_points_to_24_intervals(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.integrations.electricity_maps.urlopen",
        lambda request, *, timeout: FakeResponse(forecast_payload(25)),
    )
    provider = ElectricityMapsHttpClient(token=SecretStr("synthetic-test-token"))

    normalized = await fetch_normalized_grid_forecast(provider, zone="IN", horizon_hours=24)

    assert len(normalized.points) == 24
    assert provider.is_synthetic is False
    assert "synthetic" not in normalized.source_snapshot
    assert len(normalized.source_snapshot["forecast"]) == 25


@pytest.mark.parametrize(
    ("invalid_case", "expected_code"),
    [
        ("interior_gap", "integration_incomplete_forecast"),
        ("interior_duplicate", "integration_invalid_response"),
        ("endpoint_duplicate", "integration_invalid_response"),
        ("endpoint_misaligned", "integration_invalid_response"),
        ("endpoint_factor_method", "integration_invalid_response"),
        ("endpoint_invalid_value", "integration_invalid_response"),
    ],
)
def test_inclusive_forecast_validates_all_points_before_trimming(
    invalid_case: str, expected_code: str
) -> None:
    payload = forecast_payload(25)
    forecast = payload["forecast"]
    assert isinstance(forecast, list)
    if invalid_case == "interior_gap":
        forecast[6]["datetime"] = "2026-10-02T09:00:00Z"
    elif invalid_case == "interior_duplicate":
        forecast[6]["datetime"] = forecast[5]["datetime"]
    elif invalid_case == "endpoint_duplicate":
        forecast[-1]["datetime"] = forecast[-2]["datetime"]
    elif invalid_case == "endpoint_misaligned":
        forecast[-1]["datetime"] = "2026-10-02T08:30:00Z"
    elif invalid_case == "endpoint_factor_method":
        forecast[-1]["emissionFactorType"] = "direct"
    else:
        forecast[-1]["carbonIntensity"] = -1

    with pytest.raises(ElectricityMapsProviderError) as caught:
        normalize_electricity_maps_forecast(payload, expected_zone="IN", horizon_hours=24)

    assert caught.value.code == expected_code
    assert caught.value.retryable is False


@pytest.mark.parametrize("count", [23, 26])
def test_forecast_rejects_unexpected_horizon_lengths(count: int) -> None:
    with pytest.raises(ElectricityMapsProviderError) as caught:
        normalize_electricity_maps_forecast(
            forecast_payload(count), expected_zone="IN", horizon_hours=24
        )
    assert caught.value.code == "integration_incomplete_forecast"


def test_excluded_endpoint_remains_bound_by_raw_response_checksum() -> None:
    payload = forecast_payload(25)
    first = normalize_electricity_maps_forecast(payload, expected_zone="IN", horizon_hours=24)
    forecast = payload["forecast"]
    assert isinstance(forecast, list)
    forecast[-1]["carbonIntensity"] += 1

    changed = normalize_electricity_maps_forecast(payload, expected_zone="IN", horizon_hours=24)

    assert first.points == changed.points
    assert first.response_checksum != changed.response_checksum


def test_forecast_contract_rejects_non_hour_aligned_points() -> None:
    payload = forecast_payload()
    forecast = payload["forecast"]
    assert isinstance(forecast, list)
    forecast[3]["datetime"] = "2026-10-01T11:30:00Z"

    with pytest.raises(ElectricityMapsProviderError) as caught:
        normalize_electricity_maps_forecast(
            payload,
            expected_zone="IN",
            horizon_hours=6,
        )

    assert caught.value.code == "integration_invalid_response"
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-01T08:30:00Z",
        "2026-10-01T08:00:01Z",
        "2026-10-01T08:00:00.000001Z",
        "2026-10-01T08:00:00+05:30",
    ],
)
def test_history_contract_rejects_non_hour_aligned_provider_points(timestamp: str) -> None:
    with pytest.raises(ValidationError, match="exact UTC hour"):
        ElectricityMapsRangePayload.model_validate(
            {
                "zone": "IN",
                "temporalGranularity": "hourly",
                "data": [
                    {
                        "zone": "IN",
                        "carbonIntensity": 400,
                        "datetime": timestamp,
                        "updatedAt": "2026-10-01T08:45:00Z",
                    }
                ],
            }
        )


def test_provider_adapters_declare_fixture_provenance() -> None:
    assert ElectricityMapsFixtureClient.is_synthetic is True
    assert ElectricityMapsHttpClient.is_synthetic is False


@pytest.mark.asyncio
async def test_forecast_estimations_cannot_be_silently_disabled() -> None:
    provider = ElectricityMapsFixtureClient(DEMO_FIXTURES)

    with pytest.raises(ElectricityMapsProviderError) as caught:
        await provider.get_carbon_intensity_forecast(
            zone="IN",
            horizon_hours=24,
            disable_estimations=True,
        )

    assert caught.value.code == "integration_forecast_estimations_required"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_forecast_fixture_lookup_loads_container_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CARBONMESH_DEMO_FIXTURE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    from app.api.routes import dispatch
    from app.modules.demo import fixtures

    bundled = tmp_path / "app" / "demo_fixtures"
    shutil.copytree(DEMO_FIXTURES, bundled)
    monkeypatch.setattr(fixtures, "__file__", str(tmp_path / "app/modules/demo/fixtures.py"))

    assert dispatch._demo_forecast_fixture_directory() == bundled
    provider = ElectricityMapsFixtureClient(dispatch._demo_forecast_fixture_directory())
    forecast = await fetch_normalized_grid_forecast(provider, zone="IN", horizon_hours=24)
    assert len(forecast.points) == 24
    assert forecast.source_snapshot["synthetic"] is True


def test_forecast_fixture_lookup_uses_canonical_data_outside_repository_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.api.routes import dispatch

    monkeypatch.delenv("CARBONMESH_DEMO_FIXTURE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert dispatch._demo_forecast_fixture_directory() == DEMO_FIXTURES


def test_forecast_fixture_lookup_preserves_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.api.routes import dispatch

    monkeypatch.setenv("CARBONMESH_DEMO_FIXTURE_DIR", str(tmp_path))
    assert dispatch._demo_forecast_fixture_directory() == tmp_path


def test_forecast_fixture_lookup_preserves_startup_when_bundle_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.api.routes import dispatch

    def missing_bundle() -> Path:
        raise RuntimeError("Synthetic missing bundle")

    monkeypatch.delenv("CARBONMESH_DEMO_FIXTURE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dispatch, "_fixture_directory", missing_bundle)
    assert dispatch._demo_forecast_fixture_directory() == tmp_path / "data" / "demo"
