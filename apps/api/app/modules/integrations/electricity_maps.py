from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import SecretStr

from app.core.config import get_settings

ELECTRICITY_MAPS_BASE_URL = "https://api.electricitymaps.com/v4"
MAX_RESPONSE_BYTES = 2_000_000


class ElectricityMapsProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "electricity_maps_unavailable",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ElectricityMapsProvider(Protocol):
    is_synthetic: bool

    async def list_zones(self) -> Mapping[str, Any]: ...

    async def get_carbon_intensity_range(
        self,
        *,
        zone: str,
        start: datetime,
        end: datetime,
        disable_estimations: bool = False,
    ) -> Mapping[str, Any]: ...

    async def get_carbon_intensity_forecast(
        self,
        *,
        zone: str,
        horizon_hours: int = 24,
        disable_estimations: bool = False,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ElectricityMapsHttpClient:
    """Small async facade over the V4 API with a hard timeout and response cap."""

    token: SecretStr | None
    timeout_seconds: float = 10.0
    is_synthetic: ClassVar[bool] = False

    async def list_zones(self) -> Mapping[str, Any]:
        return await self._get_json("/zones")

    async def get_carbon_intensity_range(
        self,
        *,
        zone: str,
        start: datetime,
        end: datetime,
        disable_estimations: bool = False,
    ) -> Mapping[str, Any]:
        return await self._get_json(
            "/carbon-intensity/past-range",
            params={
                "zone": zone,
                "start": _api_datetime(start),
                "end": _api_datetime(end),
                "temporalGranularity": "hourly",
                "emissionFactorType": "lifecycle",
                "flowTraced": "true",
                "disableCallerLookup": "true",
                "disableEstimations": str(disable_estimations).lower(),
            },
        )

    async def get_carbon_intensity_forecast(
        self,
        *,
        zone: str,
        horizon_hours: int = 24,
        disable_estimations: bool = False,
    ) -> Mapping[str, Any]:
        if horizon_hours not in {6, 24, 48, 72}:
            raise ElectricityMapsProviderError(
                "Electricity Maps forecast horizon must be 6, 24, 48, or 72 hours.",
                code="integration_invalid_forecast_horizon",
                retryable=False,
            )
        if disable_estimations:
            # Forecast values are estimates by definition and the v4 forecast
            # endpoint does not expose the historical disableEstimations filter.
            raise ElectricityMapsProviderError(
                "Electricity Maps forecasts require estimated values.",
                code="integration_forecast_estimations_required",
                retryable=False,
            )
        return await self._get_json(
            "/carbon-intensity/forecast",
            params={
                "zone": zone,
                "horizonHours": str(horizon_hours),
                "temporalGranularity": "hourly",
                "disableCallerLookup": "true",
            },
        )

    async def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        if self.token is None:
            raise ElectricityMapsProviderError(
                "Electricity Maps is not configured.",
                code="integration_not_configured",
                retryable=False,
            )

        query = f"?{urlencode(params)}" if params else ""
        # The origin and paths are source-controlled; callers can only supply
        # query values, which urlencode escapes before the request is created.
        url = f"{ELECTRICITY_MAPS_BASE_URL}{path}{query}"
        token = self.token.get_secret_value()

        def request_json() -> Mapping[str, Any]:
            request = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "CarbonMesh/0.1",
                    "auth-token": token,
                },
                method="GET",
            )
            try:
                with urlopen(  # nosec B310
                    request, timeout=self.timeout_seconds
                ) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
            except HTTPError as error:
                if error.code in {401, 403}:
                    raise ElectricityMapsProviderError(
                        "Electricity Maps rejected the configured credential.",
                        code="integration_authentication_failed",
                        retryable=False,
                    ) from None
                if error.code == 429:
                    raise ElectricityMapsProviderError(
                        "Electricity Maps rate limited the request.",
                        code="integration_rate_limited",
                    ) from None
                if 400 <= error.code < 500:
                    raise ElectricityMapsProviderError(
                        "Electricity Maps rejected the request.",
                        code="integration_request_rejected",
                        retryable=False,
                    ) from None
                raise ElectricityMapsProviderError(
                    "Electricity Maps returned an unsuccessful response."
                ) from None
            except (TimeoutError, URLError, OSError):
                raise ElectricityMapsProviderError(
                    "Electricity Maps could not be reached within the configured timeout."
                ) from None

            if len(raw) > MAX_RESPONSE_BYTES:
                raise ElectricityMapsProviderError(
                    "Electricity Maps returned more data than the bounded response limit.",
                    code="integration_response_too_large",
                    retryable=False,
                )
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ElectricityMapsProviderError(
                    "Electricity Maps returned an invalid JSON response.",
                    code="integration_invalid_response",
                    retryable=False,
                ) from None
            if not isinstance(payload, dict):
                raise ElectricityMapsProviderError(
                    "Electricity Maps returned an unexpected response shape.",
                    code="integration_invalid_response",
                    retryable=False,
                )
            return payload

        for attempt in range(2):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(request_json),
                    timeout=self.timeout_seconds + 1,
                )
            except ElectricityMapsProviderError as error:
                if not error.retryable or attempt == 1:
                    raise
            except TimeoutError:
                if attempt == 1:
                    raise ElectricityMapsProviderError(
                        "Electricity Maps could not be reached within the configured timeout."
                    ) from None
        raise AssertionError("bounded provider retry loop did not terminate")  # pragma: no cover


def get_electricity_maps_client() -> ElectricityMapsProvider:
    """FastAPI dependency and deterministic test override seam."""
    settings = get_settings()
    return ElectricityMapsHttpClient(
        token=settings.electricity_maps_api_token,
        timeout_seconds=settings.electricity_maps_timeout_seconds,
    )


def _api_datetime(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
