"""Base protocol and bounded HTTP transport for model providers."""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from time import perf_counter
from typing import Any, ClassVar, Protocol

import httpx
from pydantic import SecretStr

from app.modules.agents.llm.contracts import AIProviderName, AIRequest, AIResult
from app.modules.agents.llm.errors import AIProviderError

MAX_RESPONSE_BYTES = 2_000_000


class AIModel(Protocol):
    """The only model-provider interface used by agent orchestration."""

    provider: AIProviderName
    model_id: str

    async def generate(self, request: AIRequest) -> AIResult: ...


class HTTPAIModel(ABC):
    """Shared safe HTTP behavior; subclasses only translate provider contracts."""

    provider: ClassVar[AIProviderName]

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model_id: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self.model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    async def generate(self, request: AIRequest) -> AIResult:
        started_at = perf_counter()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                payload = await self._post_json(
                    url=self._endpoint(),
                    headers=self._headers(),
                    payload=self._request_payload(request),
                )
                latency_ms = max(0, round((perf_counter() - started_at) * 1000))
                return self._parse_response(payload, latency_ms=latency_ms)
        except TimeoutError:
            raise AIProviderError(
                "The AI provider did not respond within the configured timeout.",
                provider=self.provider,
                code="ai_provider_timeout",
            ) from None

    @abstractmethod
    def _endpoint(self) -> str: ...

    @abstractmethod
    def _headers(self) -> Mapping[str, str]: ...

    @abstractmethod
    def _request_payload(self, request: AIRequest) -> Mapping[str, Any]: ...

    @abstractmethod
    def _parse_response(self, payload: Mapping[str, Any], *, latency_ms: int) -> AIResult: ...

    async def _post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        client = self._http_client or httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=False,
        )
        try:
            return await self._post_with_client(
                client,
                url=url,
                headers=headers,
                payload=payload,
            )
        finally:
            if self._http_client is None:
                await client.aclose()

    async def _post_with_client(
        self,
        client: httpx.AsyncClient,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
                timeout=self._timeout_seconds,
            ) as response:
                self._raise_for_status(response.status_code)
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise AIProviderError(
                            "The AI provider returned more data than the bounded response limit.",
                            provider=self.provider,
                            code="ai_response_too_large",
                            retryable=False,
                        )
        except AIProviderError:
            raise
        except httpx.TimeoutException:
            raise AIProviderError(
                "The AI provider did not respond within the configured timeout.",
                provider=self.provider,
                code="ai_provider_timeout",
            ) from None
        except httpx.RequestError:
            raise AIProviderError(
                "The AI provider could not be reached.",
                provider=self.provider,
            ) from None

        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AIProviderError(
                "The AI provider returned invalid JSON.",
                provider=self.provider,
                code="ai_invalid_response",
                retryable=False,
            ) from None
        if not isinstance(decoded, dict):
            raise AIProviderError(
                "The AI provider returned an unexpected response shape.",
                provider=self.provider,
                code="ai_invalid_response",
                retryable=False,
            )
        return decoded

    def _raise_for_status(self, status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code in {401, 403}:
            raise AIProviderError(
                "The AI provider rejected the configured credential.",
                provider=self.provider,
                code="ai_authentication_failed",
                retryable=False,
            )
        if status_code == 429:
            raise AIProviderError(
                "The AI provider rate limited the request.",
                provider=self.provider,
                code="ai_rate_limited",
            )
        if status_code in {408, 409} or status_code >= 500:
            raise AIProviderError(
                "The AI provider is temporarily unavailable.",
                provider=self.provider,
            )
        raise AIProviderError(
            "The AI provider rejected the request.",
            provider=self.provider,
            code="ai_request_rejected",
            retryable=False,
        )


def require_mapping(value: object, *, provider: AIProviderName) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise invalid_response(provider)
    return value


def require_list(value: object, *, provider: AIProviderName) -> list[Any]:
    if not isinstance(value, list):
        raise invalid_response(provider)
    return value


def nonnegative_int(value: object, *, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default


def optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def invalid_response(provider: AIProviderName) -> AIProviderError:
    return AIProviderError(
        "The AI provider returned an unexpected response shape.",
        provider=provider,
        code="ai_invalid_response",
        retryable=False,
    )
