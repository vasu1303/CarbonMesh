"""Credential-safe errors raised by the model-provider boundary."""

from __future__ import annotations

from app.modules.agents.llm.contracts import AIProviderName


class AIConfigurationError(RuntimeError):
    """Raised before a network call when provider configuration is incomplete."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = False


class AIProviderError(RuntimeError):
    """Sanitized provider failure safe for telemetry and API error mapping."""

    def __init__(
        self,
        message: str,
        *,
        provider: AIProviderName,
        code: str = "ai_provider_unavailable",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.retryable = retryable
