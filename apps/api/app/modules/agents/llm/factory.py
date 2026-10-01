"""Lazy, environment-driven model-provider selection."""

from __future__ import annotations

from typing import cast

import httpx
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.modules.agents.llm.anthropic import AnthropicModel
from app.modules.agents.llm.base import AIModel
from app.modules.agents.llm.contracts import AIProviderName
from app.modules.agents.llm.errors import AIConfigurationError
from app.modules.agents.llm.gemini import GeminiModel
from app.modules.agents.llm.openai import OpenAIModel
from app.modules.agents.llm.openrouter import OpenRouterModel

SUPPORTED_PROVIDERS: tuple[AIProviderName, ...] = (
    "openai",
    "gemini",
    "anthropic",
    "openrouter",
)


def build_ai_model(
    settings: Settings | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> AIModel:
    """Build the configured adapter without making a network request.

    With ``AI_PROVIDER=auto`` (or blank), selection succeeds only when exactly
    one provider credential is present. This deliberately avoids silent
    priority ordering, cross-provider failover, and surprise billing.
    """

    configured = settings or get_settings()
    provider = resolve_ai_provider(configured)
    api_key = _provider_api_key(configured, provider)
    if api_key is None:
        raise AIConfigurationError(
            f"{provider.title()} authentication is not configured.",
            code="ai_provider_not_configured",
        )
    model_id = _provider_model(configured, provider)
    if model_id is None:
        raise AIConfigurationError(
            f"{provider.title()} model is not configured.",
            code="ai_model_not_configured",
        )

    common = {
        "api_key": api_key,
        "model_id": model_id,
        "timeout_seconds": configured.ai_timeout_seconds,
        "http_client": http_client,
    }
    if provider == "openai":
        return OpenAIModel(**common)
    if provider == "gemini":
        return GeminiModel(**common)
    if provider == "anthropic":
        return AnthropicModel(**common)
    return OpenRouterModel(
        **common,
        site_url=configured.openrouter_site_url,
        app_name=configured.openrouter_app_name,
    )


def resolve_ai_provider(settings: Settings) -> AIProviderName:
    requested = (settings.ai_provider or "auto").lower()
    if requested != "auto":
        if requested not in SUPPORTED_PROVIDERS:
            raise AIConfigurationError(
                "AI_PROVIDER must be one of: auto, openai, gemini, anthropic, openrouter.",
                code="ai_provider_invalid",
            )
        return cast(AIProviderName, requested)

    configured = [
        provider
        for provider in SUPPORTED_PROVIDERS
        if _provider_api_key(settings, provider) is not None
    ]
    if not configured:
        raise AIConfigurationError(
            "No AI provider authentication is configured.",
            code="ai_provider_not_configured",
        )
    if len(configured) > 1:
        raise AIConfigurationError(
            "Multiple AI provider credentials are configured; set AI_PROVIDER explicitly.",
            code="ai_provider_ambiguous",
        )
    return configured[0]


def _provider_api_key(settings: Settings, provider: AIProviderName) -> SecretStr | None:
    return {
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
        "anthropic": settings.anthropic_api_key,
        "openrouter": settings.openrouter_api_key,
    }[provider]


def _provider_model(settings: Settings, provider: AIProviderName) -> str | None:
    return {
        "openai": settings.openai_model,
        "gemini": settings.gemini_model,
        "anthropic": settings.anthropic_model,
        "openrouter": settings.openrouter_model,
    }[provider]
