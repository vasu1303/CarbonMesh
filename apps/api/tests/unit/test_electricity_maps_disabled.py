from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.observability import begin_external_usage, take_external_usage
from app.main import app
from app.modules.dispatch.schemas import ForecastSyncRequest
from app.modules.integrations.electricity_maps import (
    ElectricityMapsHttpClient,
    ElectricityMapsProviderError,
    get_electricity_maps_client,
)
from app.modules.integrations.schemas import GridIntensitySyncRequest


def test_live_calls_default_off_and_fixtures_stay_explicit(monkeypatch):
    monkeypatch.delenv("ELECTRICITY_MAPS_LIVE_ENABLED", raising=False)
    assert Settings().electricity_maps_live_enabled is False
    assert get_settings().electricity_maps_live_enabled is False
    assert GridIntensitySyncRequest().mode == "live"
    assert ForecastSyncRequest.model_fields["source_mode"].default == "live"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["zones", "history", "forecast"])
@pytest.mark.parametrize("configured_token", [None, SecretStr("synthetic-disabled-token")])
async def test_disabled_client_never_attempts_http(monkeypatch, operation, configured_token):
    network = Mock(side_effect=AssertionError("Disabled provider attempted network access"))
    monkeypatch.setattr("app.modules.integrations.electricity_maps.urlopen", network)
    client = ElectricityMapsHttpClient(token=configured_token)
    begin_external_usage()
    with pytest.raises(ElectricityMapsProviderError) as caught:
        if operation == "zones":
            await client.list_zones()
        elif operation == "history":
            start = datetime(2026, 7, 1, tzinfo=UTC)
            await client.get_carbon_intensity_range(
                zone="IN",
                start=start,
                end=start + timedelta(hours=1),
            )
        else:
            await client.get_carbon_intensity_forecast(zone="IN")
    assert caught.value.code == "integration_live_disabled"
    assert caught.value.retryable is False
    network.assert_not_called()
    usage = take_external_usage()
    assert usage.api_calls == usage.retry_count == 0


@pytest.mark.asyncio
async def test_existing_client_respects_switch_disabled_after_creation(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_MAPS_LIVE_ENABLED", "true")
    monkeypatch.setenv("ELECTRICITY_MAPS_API_TOKEN", "synthetic-disabled-token")
    provider = get_electricity_maps_client()
    monkeypatch.setenv("ELECTRICITY_MAPS_LIVE_ENABLED", "false")
    network = Mock(side_effect=AssertionError("Unexpected network access"))
    monkeypatch.setattr("app.modules.integrations.electricity_maps.urlopen", network)
    with pytest.raises(ElectricityMapsProviderError, match="disabled"):
        await provider.list_zones()
    network.assert_not_called()


def test_disabled_provider_route_returns_explicit_safe_error(monkeypatch):
    network = Mock(side_effect=AssertionError("Unexpected network access"))
    monkeypatch.setattr("app.modules.integrations.electricity_maps.urlopen", network)
    response = TestClient(app).post("/api/integrations/electricity-maps/test")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "integration_live_disabled"
    assert response.json()["detail"]["retryable"] is False
    network.assert_not_called()
