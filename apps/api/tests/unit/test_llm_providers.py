from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.modules.agents.llm import AIMessage, AIRequest, build_ai_model
from app.modules.agents.llm.errors import AIProviderError
from app.modules.agents.llm.openai import OpenAIModel

Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


def _request() -> AIRequest:
    return AIRequest(
        system_instruction="Use verified facts only.",
        messages=(
            AIMessage(role="user", content="Measure Plant B."),
            AIMessage(role="assistant", content="I will use the provided facts."),
            AIMessage(role="user", content="Return the result."),
        ),
        max_output_tokens=321,
        temperature=0.2,
    )


async def _generate(
    settings: Settings,
    handler: Handler,
):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = build_ai_model(settings, http_client=client)
        return await model.generate(_request())


@pytest.mark.asyncio
async def test_openai_adapter_uses_responses_api_and_normalizes_result() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "resp_123",
                "model": "response-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "Verified"},
                            {"type": "output_text", "text": " result"},
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 4,
                    "total_tokens": 15,
                    "input_tokens_details": {"cached_tokens": 3},
                },
            },
        )

    result = await _generate(
        Settings(
            ai_provider="openai",
            openai_api_key="openai-secret",
            openai_model="configured-openai-model",
        ),
        handler,
    )

    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["authorization"] == "Bearer openai-secret"
    assert captured["body"] == {
        "model": "configured-openai-model",
        "input": [
            {"role": "user", "content": "Measure Plant B."},
            {"role": "assistant", "content": "I will use the provided facts."},
            {"role": "user", "content": "Return the result."},
        ],
        "max_output_tokens": 321,
        "store": False,
        "instructions": "Use verified facts only.",
        "temperature": 0.2,
    }
    assert result.provider == "openai"
    assert result.model_id == "response-model"
    assert result.text == "Verified result"
    assert result.provider_request_id == "resp_123"
    assert result.usage is not None
    assert result.usage.model_dump() == {
        "input_tokens": 11,
        "output_tokens": 4,
        "total_tokens": 15,
        "cached_input_tokens": 3,
    }


@pytest.mark.asyncio
async def test_gemini_adapter_maps_roles_and_system_instruction() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers["x-goog-api-key"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "responseId": "gemini-123",
                "modelVersion": "gemini-response-model",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [{"text": "Gemini result"}],
                        },
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 5,
                    "totalTokenCount": 15,
                    "cachedContentTokenCount": 2,
                },
            },
        )

    result = await _generate(
        Settings(
            ai_provider="gemini",
            gemini_api_key="gemini-secret",
            gemini_model="models/gemini test model",
        ),
        handler,
    )

    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini%20test%20model:generateContent"
    )
    assert captured["key"] == "gemini-secret"
    assert captured["body"]["contents"][1]["role"] == "model"
    assert captured["body"]["systemInstruction"] == {
        "parts": [{"text": "Use verified facts only."}]
    }
    assert captured["body"]["generationConfig"] == {
        "maxOutputTokens": 321,
        "temperature": 0.2,
    }
    assert result.text == "Gemini result"
    assert result.finish_reason == "STOP"
    assert result.usage is not None
    assert result.usage.cached_input_tokens == 2


@pytest.mark.asyncio
async def test_anthropic_adapter_uses_top_level_system_and_normalizes_cached_usage() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_123",
                "model": "claude-response-model",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "thinking", "thinking": "not returned"},
                    {"type": "text", "text": "Anthropic result"},
                ],
                "usage": {
                    "input_tokens": 8,
                    "cache_creation_input_tokens": 2,
                    "cache_read_input_tokens": 3,
                    "output_tokens": 4,
                },
            },
        )

    result = await _generate(
        Settings(
            ai_provider="anthropic",
            anthropic_api_key="anthropic-secret",
            anthropic_model="configured-anthropic-model",
        ),
        handler,
    )

    assert captured["headers"]["x-api-key"] == "anthropic-secret"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["body"]["system"] == "Use verified facts only."
    assert captured["body"]["max_tokens"] == 321
    assert all(message["role"] != "system" for message in captured["body"]["messages"])
    assert result.text == "Anthropic result"
    assert result.usage is not None
    assert result.usage.input_tokens == 13
    assert result.usage.output_tokens == 4
    assert result.usage.total_tokens == 17
    assert result.usage.cached_input_tokens == 3


@pytest.mark.asyncio
async def test_openrouter_adapter_uses_distinct_endpoint_and_attribution_headers() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "generation-123",
                "model": "provider/response-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "OpenRouter result"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 6,
                    "total_tokens": 15,
                },
            },
        )

    result = await _generate(
        Settings(
            ai_provider="openrouter",
            openrouter_api_key="openrouter-secret",
            openrouter_model="provider/configured-model",
            openrouter_site_url="https://carbonmesh.example",
            openrouter_app_name="CarbonMesh Tests",
        ),
        handler,
    )

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer openrouter-secret"
    assert captured["headers"]["http-referer"] == "https://carbonmesh.example"
    assert captured["headers"]["x-openrouter-title"] == "CarbonMesh Tests"
    assert captured["body"]["messages"][0] == {
        "role": "system",
        "content": "Use verified facts only.",
    }
    assert captured["body"]["max_completion_tokens"] == 321
    assert "max_tokens" not in captured["body"]
    assert result.provider == "openrouter"
    assert result.text == "OpenRouter result"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (401, "ai_authentication_failed", False),
        (429, "ai_rate_limited", True),
        (500, "ai_provider_unavailable", True),
        (400, "ai_request_rejected", False),
    ],
)
async def test_http_failures_are_normalized_without_leaking_provider_bodies(
    status_code: int,
    expected_code: str,
    retryable: bool,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"error": "provider-body-secret", "credential": "openai-secret"},
        )

    with pytest.raises(AIProviderError) as caught:
        await _generate(
            Settings(
                ai_provider="openai",
                openai_api_key="openai-secret",
                openai_model="configured-model",
            ),
            handler,
        )

    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable
    assert caught.value.provider == "openai"
    assert "provider-body-secret" not in str(caught.value)
    assert "openai-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_network_exception_is_sanitized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider leaked detail", request=request)

    with pytest.raises(AIProviderError) as caught:
        await _generate(
            Settings(
                ai_provider="openai",
                openai_api_key="openai-secret",
                openai_model="configured-model",
            ),
            handler,
        )

    assert caught.value.code == "ai_provider_timeout"
    assert caught.value.retryable is True
    assert "provider leaked detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_timeout_is_an_end_to_end_deadline() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = OpenAIModel(
            api_key=SecretStr("openai-secret"),
            model_id="configured-model",
            timeout_seconds=0.01,
            http_client=client,
        )
        with pytest.raises(AIProviderError) as caught:
            await model.generate(_request())

    assert caught.value.code == "ai_provider_timeout"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_openai_failed_response_is_a_sanitized_typed_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "failed",
                "error": {"message": "provider-body-secret"},
                "output": [],
            },
        )

    with pytest.raises(AIProviderError) as caught:
        await _generate(
            Settings(
                ai_provider="openai",
                openai_api_key="openai-secret",
                openai_model="configured-model",
            ),
            handler,
        )

    assert caught.value.code == "ai_generation_failed"
    assert caught.value.retryable is False
    assert "provider-body-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_openai_incomplete_response_returns_exact_partial_text_and_reason() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_incomplete",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": " partial"},
                            {"type": "output_text", "text": " result "},
                        ],
                    }
                ],
            },
        )

    result = await _generate(
        Settings(
            ai_provider="openai",
            openai_api_key="openai-secret",
            openai_model="configured-model",
        ),
        handler,
    )

    assert result.text == " partial result "
    assert result.finish_reason == "max_output_tokens"


@pytest.mark.asyncio
async def test_gemini_blocked_prompt_is_a_nonretryable_typed_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})

    with pytest.raises(AIProviderError) as caught:
        await _generate(
            Settings(
                ai_provider="gemini",
                gemini_api_key="gemini-secret",
                gemini_model="configured-model",
            ),
            handler,
        )

    assert caught.value.code == "ai_content_blocked"
    assert caught.value.retryable is False
