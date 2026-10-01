"""Anthropic Messages API adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.modules.agents.llm.base import (
    HTTPAIModel,
    invalid_response,
    nonnegative_int,
    optional_text,
    require_list,
)
from app.modules.agents.llm.contracts import AIProviderName, AIRequest, AIResult, AITokenUsage
from app.modules.agents.llm.errors import AIProviderError

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"


class AnthropicModel(HTTPAIModel):
    provider: ClassVar[AIProviderName] = "anthropic"

    def _endpoint(self) -> str:
        return ANTHROPIC_MESSAGES_URL

    def _headers(self) -> Mapping[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": self._api_key.get_secret_value(),
            "anthropic-version": ANTHROPIC_API_VERSION,
            "User-Agent": "CarbonMesh/0.1",
        }

    def _request_payload(self, request: AIRequest) -> Mapping[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": request.max_output_tokens,
            "messages": [message.model_dump(mode="json") for message in request.messages],
        }
        if request.system_instruction is not None:
            payload["system"] = request.system_instruction
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        return payload

    def _parse_response(self, payload: Mapping[str, Any], *, latency_ms: int) -> AIResult:
        finish_reason = optional_text(payload.get("stop_reason"))
        if finish_reason == "refusal":
            raise AIProviderError(
                "The AI provider declined to generate this response.",
                provider=self.provider,
                code="ai_content_blocked",
                retryable=False,
            )
        content = require_list(payload.get("content"), provider=self.provider)
        text = "".join(
            block["text"]
            for block_value in content
            if isinstance(block_value, dict)
            and isinstance((block := block_value).get("text"), str)
            and block.get("type") == "text"
            and block["text"]
        )
        if not text.strip():
            raise invalid_response(self.provider)

        usage_value = payload.get("usage")
        usage = None
        if isinstance(usage_value, dict):
            base_input_tokens = nonnegative_int(usage_value.get("input_tokens"))
            cache_creation_tokens = nonnegative_int(
                usage_value.get("cache_creation_input_tokens")
            )
            cache_read_tokens = nonnegative_int(usage_value.get("cache_read_input_tokens"))
            input_tokens = base_input_tokens + cache_creation_tokens + cache_read_tokens
            output_tokens = nonnegative_int(usage_value.get("output_tokens"))
            usage = AITokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cached_input_tokens=cache_read_tokens,
            )

        return AIResult(
            provider=self.provider,
            model_id=optional_text(payload.get("model")) or self.model_id,
            text=text,
            usage=usage,
            provider_request_id=optional_text(payload.get("id")),
            finish_reason=finish_reason,
            latency_ms=latency_ms,
        )
