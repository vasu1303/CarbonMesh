"""Google Gemini generateContent adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import quote

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

GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
BLOCKED_FINISH_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}


class GeminiModel(HTTPAIModel):
    provider: ClassVar[AIProviderName] = "gemini"

    def _endpoint(self) -> str:
        # Google documentation shows both the bare model ID and the full
        # ``models/{id}`` resource name. Accept either without allowing an
        # arbitrary path to alter the source-controlled API origin.
        encoded_model = quote(self.model_id.removeprefix("models/"), safe="")
        return f"{GEMINI_API_ROOT}/{encoded_model}:generateContent"

    def _headers(self) -> Mapping[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key.get_secret_value(),
            "User-Agent": "CarbonMesh/0.1",
        }

    def _request_payload(self, request: AIRequest) -> Mapping[str, Any]:
        generation_config: dict[str, Any] = {
            "maxOutputTokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "model" if message.role == "assistant" else "user",
                    "parts": [{"text": message.content}],
                }
                for message in request.messages
            ],
            "generationConfig": generation_config,
        }
        if request.system_instruction is not None:
            payload["systemInstruction"] = {"parts": [{"text": request.system_instruction}]}
        return payload

    def _parse_response(self, payload: Mapping[str, Any], *, latency_ms: int) -> AIResult:
        prompt_feedback = payload.get("promptFeedback")
        if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
            raise self._blocked_error()

        candidates = require_list(payload.get("candidates"), provider=self.provider)
        if not candidates:
            raise invalid_response(self.provider)
        candidate = require_mapping(candidates[0], provider=self.provider)
        finish_reason = optional_text(candidate.get("finishReason"))
        if finish_reason in BLOCKED_FINISH_REASONS:
            raise self._blocked_error()
        content = require_mapping(candidate.get("content"), provider=self.provider)
        parts = require_list(content.get("parts"), provider=self.provider)
        text = "".join(
            part["text"]
            for part_value in parts
            if isinstance(part_value, dict)
            and isinstance((part := part_value).get("text"), str)
            and part["text"]
        )
        if not text.strip():
            raise invalid_response(self.provider)

        usage_value = payload.get("usageMetadata")
        usage = None
        if isinstance(usage_value, dict):
            input_tokens = nonnegative_int(usage_value.get("promptTokenCount"))
            output_tokens = nonnegative_int(usage_value.get("candidatesTokenCount"))
            usage = AITokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=nonnegative_int(
                    usage_value.get("totalTokenCount"),
                    default=input_tokens + output_tokens,
                ),
                cached_input_tokens=nonnegative_int(
                    usage_value.get("cachedContentTokenCount")
                ),
            )

        return AIResult(
            provider=self.provider,
            model_id=optional_text(payload.get("modelVersion")) or self.model_id,
            text=text,
            usage=usage,
            provider_request_id=optional_text(payload.get("responseId")),
            finish_reason=finish_reason,
            latency_ms=latency_ms,
        )

    def _blocked_error(self) -> AIProviderError:
        return AIProviderError(
            "The AI provider declined to generate this response.",
            provider=self.provider,
            code="ai_content_blocked",
            retryable=False,
        )
