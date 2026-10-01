"""Provider-neutral contracts for bounded model calls."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AIProviderName = Literal["openai", "gemini", "anthropic", "openrouter"]
AIMessageRole = Literal["user", "assistant"]

MAX_MESSAGE_COUNT = 50
MAX_PROMPT_CHARACTERS = 100_000


class AIMessage(BaseModel):
    """One provider-neutral conversation message.

    System instructions are deliberately separate on ``AIRequest`` because the
    four providers encode them differently.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    role: AIMessageRole
    content: str = Field(min_length=1, max_length=MAX_PROMPT_CHARACTERS, repr=False)


class AIRequest(BaseModel):
    """A bounded text-generation request accepted by every adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    messages: tuple[AIMessage, ...] = Field(min_length=1, max_length=MAX_MESSAGE_COUNT)
    system_instruction: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_PROMPT_CHARACTERS,
        repr=False,
    )
    max_output_tokens: int = Field(default=1024, ge=1, le=32_768)
    temperature: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def enforce_total_prompt_limit(self) -> AIRequest:
        character_count = len(self.system_instruction or "") + sum(
            len(message.content) for message in self.messages
        )
        if character_count > MAX_PROMPT_CHARACTERS:
            raise ValueError(
                f"The combined prompt cannot exceed {MAX_PROMPT_CHARACTERS} characters."
            )
        return self


class AITokenUsage(BaseModel):
    """Token accounting normalized across model providers."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)


class AIResult(BaseModel):
    """Safe normalized result; the raw provider response is never retained."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    provider: AIProviderName
    model_id: str
    text: str = Field(min_length=1, repr=False)
    usage: AITokenUsage | None = None
    provider_request_id: str | None = None
    finish_reason: str | None = None
    latency_ms: int = Field(ge=0)
