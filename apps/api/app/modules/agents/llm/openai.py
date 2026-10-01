"""OpenAI Responses API adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

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

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


class OpenAIModel(HTTPAIModel):
    provider: ClassVar[AIProviderName] = "openai"

    def _endpoint(self) -> str:
        return OPENAI_RESPONSES_URL

    def _headers(self) -> Mapping[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "User-Agent": "CarbonMesh/0.1",
        }

    def _request_payload(self, request: AIRequest) -> Mapping[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "input": [message.model_dump(mode="json") for message in request.messages],
            "max_output_tokens": request.max_output_tokens,
            "store": False,
        }
        if request.system_instruction is not None:
            payload["instructions"] = request.system_instruction
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        return payload

    def _parse_response(self, payload: Mapping[str, Any], *, latency_ms: int) -> AIResult:
        status = optional_text(payload.get("status"))
        if payload.get("error") is not None or status in {"failed", "cancelled"}:
            raise AIProviderError(
                "The AI provider could not complete the request.",
                provider=self.provider,
                code="ai_generation_failed",
                retryable=False,
            )
        if status in {"queued", "in_progress"}:
            raise AIProviderError(
                "The AI provider did not return a terminal response.",
                provider=self.provider,
                code="ai_generation_not_complete",
            )

        output = require_list(payload.get("output"), provider=self.provider)
        text_parts: list[str] = []
        refused = False
        for item_value in output:
            item = require_mapping(item_value, provider=self.provider)
            if item.get("type") != "message":
                continue
            for content_value in require_list(item.get("content"), provider=self.provider):
                content = require_mapping(content_value, provider=self.provider)
                content_type = content.get("type")
                if content_type == "output_text" and isinstance(content.get("text"), str):
                    text_parts.append(content["text"])
                elif content_type == "refusal":
                    refused = True

        if refused:
            raise AIProviderError(
                "The AI provider declined to generate this response.",
                provider=self.provider,
                code="ai_content_blocked",
                retryable=False,
            )
        text = "".join(part for part in text_parts if part)
        if not text.strip():
            if status == "incomplete":
                raise AIProviderError(
                    "The AI provider returned an incomplete response without usable text.",
                    provider=self.provider,
                    code="ai_generation_incomplete",
                    retryable=False,
                )
            raise invalid_response(self.provider)

        usage_value = payload.get("usage")
        usage = None
        if isinstance(usage_value, dict):
            details = usage_value.get("input_tokens_details")
            cached_tokens = (
                nonnegative_int(details.get("cached_tokens")) if isinstance(details, dict) else 0
            )
            input_tokens = nonnegative_int(usage_value.get("input_tokens"))
            output_tokens = nonnegative_int(usage_value.get("output_tokens"))
            usage = AITokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=nonnegative_int(
                    usage_value.get("total_tokens"),
                    default=input_tokens + output_tokens,
                ),
                cached_input_tokens=cached_tokens,
            )

        incomplete = payload.get("incomplete_details")
        finish_reason = status
        if isinstance(incomplete, dict):
            finish_reason = optional_text(incomplete.get("reason")) or finish_reason

        return AIResult(
            provider=self.provider,
            model_id=optional_text(payload.get("model")) or self.model_id,
            text=text,
            usage=usage,
            provider_request_id=optional_text(payload.get("id")),
            finish_reason=finish_reason,
            latency_ms=latency_ms,
        )
