"""OpenRouter chat-completions adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx
from pydantic import SecretStr

from app.modules.agents.llm.base import (
    HTTPAIModel,
    invalid_response,
    nonnegative_int,
    optional_text,
    require_list,
    require_mapping,
)
from app.modules.agents.llm.contracts import AIProviderName, AIRequest, AIResult, AITokenUsage
from app.modules.agents.llm.errors import AIProviderError

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterModel(HTTPAIModel):
    provider: ClassVar[AIProviderName] = "openrouter"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model_id: str,
        timeout_seconds: float,
        site_url: str | None = None,
        app_name: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model_id=model_id,
            timeout_seconds=timeout_seconds,
            http_client=http_client,
        )
        self._site_url = site_url
        self._app_name = app_name

    def _endpoint(self) -> str:
        return OPENROUTER_CHAT_URL

    def _headers(self) -> Mapping[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "User-Agent": "CarbonMesh/0.1",
        }
        if self._site_url is not None:
            headers["HTTP-Referer"] = self._site_url
        if self._app_name is not None:
            headers["X-OpenRouter-Title"] = self._app_name
        return headers

    def _request_payload(self, request: AIRequest) -> Mapping[str, Any]:
        messages: list[dict[str, str]] = []
        if request.system_instruction is not None:
            messages.append({"role": "system", "content": request.system_instruction})
        messages.extend(message.model_dump(mode="json") for message in request.messages)
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "max_completion_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        return payload

    def _parse_response(self, payload: Mapping[str, Any], *, latency_ms: int) -> AIResult:
        if payload.get("error") is not None:
            raise AIProviderError(
                "The AI provider could not complete the request.",
                provider=self.provider,
                code="ai_generation_failed",
                retryable=False,
            )
        choices = require_list(payload.get("choices"), provider=self.provider)
        if not choices:
            raise invalid_response(self.provider)
        choice = require_mapping(choices[0], provider=self.provider)
        message = require_mapping(choice.get("message"), provider=self.provider)
        if message.get("refusal"):
            raise AIProviderError(
                "The AI provider declined to generate this response.",
                provider=self.provider,
                code="ai_content_blocked",
                retryable=False,
            )
        text = self._message_text(message.get("content"))
        if not text.strip():
            raise invalid_response(self.provider)

        usage_value = payload.get("usage")
        usage = None
        if isinstance(usage_value, dict):
            input_tokens = nonnegative_int(usage_value.get("prompt_tokens"))
            output_tokens = nonnegative_int(usage_value.get("completion_tokens"))
            details = usage_value.get("prompt_tokens_details")
            cached_tokens = (
                nonnegative_int(details.get("cached_tokens")) if isinstance(details, dict) else 0
            )
            usage = AITokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=nonnegative_int(
                    usage_value.get("total_tokens"),
                    default=input_tokens + output_tokens,
                ),
                cached_input_tokens=cached_tokens,
            )

        return AIResult(
            provider=self.provider,
            model_id=optional_text(payload.get("model")) or self.model_id,
            text=text,
            usage=usage,
            provider_request_id=optional_text(payload.get("id")),
            finish_reason=optional_text(choice.get("finish_reason")),
            latency_ms=latency_ms,
        )

    def _message_text(self, content: object) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        return "".join(
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
            and part["text"]
        )
