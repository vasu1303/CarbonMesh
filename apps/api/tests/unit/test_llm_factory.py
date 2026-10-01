from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.modules.agents.llm import AIMessage, AIRequest, build_ai_model
from app.modules.agents.llm.errors import AIConfigurationError


@pytest.mark.parametrize(
    ("provider", "key_field", "model_field"),
    [
        ("openai", "openai_api_key", "openai_model"),
        ("gemini", "gemini_api_key", "gemini_model"),
        ("anthropic", "anthropic_api_key", "anthropic_model"),
        ("openrouter", "openrouter_api_key", "openrouter_model"),
    ],
)
def test_factory_auto_selects_the_only_authenticated_provider(
    provider: str,
    key_field: str,
    model_field: str,
) -> None:
    settings = Settings(**{key_field: "top-secret-key", model_field: "configured-model"})

    model = build_ai_model(settings)

    assert model.provider == provider
    assert model.model_id == "configured-model"
    assert "top-secret-key" not in repr(settings)
    assert "top-secret-key" not in repr(model)


def test_factory_requires_explicit_provider_when_multiple_keys_exist() -> None:
    settings = Settings(
        openai_api_key="openai-secret",
        openai_model="openai-model",
        gemini_api_key="gemini-secret",
        gemini_model="gemini-model",
    )

    with pytest.raises(AIConfigurationError) as caught:
        build_ai_model(settings)

    assert caught.value.code == "ai_provider_ambiguous"
    assert "openai-secret" not in str(caught.value)
    assert "gemini-secret" not in str(caught.value)


def test_explicit_provider_selects_matching_credentials_when_several_exist() -> None:
    settings = Settings(
        ai_provider="GEMINI",
        openai_api_key="openai-secret",
        openai_model="openai-model",
        gemini_api_key="gemini-secret",
        gemini_model="gemini-model",
    )

    model = build_ai_model(settings)

    assert model.provider == "gemini"
    assert model.model_id == "gemini-model"


@pytest.mark.parametrize(
    ("settings", "code"),
    [
        (Settings(), "ai_provider_not_configured"),
        (Settings(ai_provider="unknown"), "ai_provider_invalid"),
        (Settings(ai_provider="openai"), "ai_provider_not_configured"),
        (
            Settings(ai_provider="openai", openai_api_key="secret"),
            "ai_model_not_configured",
        ),
    ],
)
def test_factory_reports_safe_configuration_errors(settings: Settings, code: str) -> None:
    with pytest.raises(AIConfigurationError) as caught:
        build_ai_model(settings)

    assert caught.value.code == code
    assert caught.value.retryable is False
    assert "secret" not in str(caught.value)


def test_blank_credentials_and_models_are_treated_as_unconfigured() -> None:
    settings = Settings(openai_api_key="   ", openai_model="   ")

    assert settings.openai_api_key is None
    assert settings.openai_model is None


def test_factory_reads_provider_authentication_from_environment(monkeypatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_PROVIDER", "auto")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "environment-secret")
    monkeypatch.setenv("ANTHROPIC_MODEL", "environment-model")

    model = build_ai_model(get_settings())

    assert model.provider == "anthropic"
    assert model.model_id == "environment-model"


def test_openrouter_attribution_headers_reject_newlines() -> None:
    with pytest.raises(ValidationError, match="cannot contain newlines"):
        Settings(openrouter_app_name="CarbonMesh\r\nInjected: value")


@pytest.mark.parametrize("timeout", [0.99, 30.01])
def test_ai_timeout_is_bounded(timeout: float) -> None:
    with pytest.raises(ValidationError, match="between 1 and 30"):
        Settings(ai_timeout_seconds=timeout)


def test_ai_request_rejects_unknown_fields_and_oversized_combined_prompt() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AIRequest(
            messages=(AIMessage(role="user", content="hello"),),
            unexpected=True,  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError, match="combined prompt"):
        AIRequest(
            system_instruction="a" * 60_000,
            messages=(AIMessage(role="user", content="b" * 50_000),),
        )


def test_prompt_content_is_hidden_from_representations() -> None:
    request = AIRequest(
        system_instruction="private system prompt",
        messages=(AIMessage(role="user", content="private user prompt"),),
    )

    representation = repr(request)
    assert "private system prompt" not in representation
    assert "private user prompt" not in representation
