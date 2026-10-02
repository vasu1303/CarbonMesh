"""Strict structured-output execution over the provider-neutral model boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from app.modules.agents.budget import RuntimeBudgetCounter, RuntimeBudgetExhausted
from app.modules.agents.llm.base import AIModel
from app.modules.agents.llm.contracts import AIMessage, AIRequest, AIResult

MAX_STRUCTURED_RESPONSE_CHARACTERS = 100_000


class StructuredOutputValidationError(RuntimeError):
    """The selected provider did not return the requested strict JSON contract."""

    code = "ai_structured_output_invalid"


@dataclass(frozen=True, slots=True)
class ProviderLifecycleEvent:
    event_name: str
    stage: str
    provider: str
    model_id: str
    attempt: int
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    provider_request_id: str | None = None
    finish_reason: str | None = None
    error_code: str | None = None


ProviderObserver = Callable[[ProviderLifecycleEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class StructuredModelResult[StructuredValue: BaseModel]:
    value: StructuredValue
    provider_results: tuple[AIResult, ...]
    repaired: bool

    @property
    def input_tokens(self) -> int:
        return sum(item.usage.input_tokens for item in self.provider_results if item.usage)

    @property
    def output_tokens(self) -> int:
        return sum(item.usage.output_tokens for item in self.provider_results if item.usage)

    @property
    def cached_input_tokens(self) -> int:
        return sum(
            item.usage.cached_input_tokens for item in self.provider_results if item.usage
        )


class StructuredModelRunner:
    """Make one selected provider produce an exact Pydantic JSON object.

    A malformed response can trigger one budgeted repair. Provider selection is
    performed before this class is constructed, so this runner cannot silently
    fail over to another vendor.
    """

    def __init__(self, model: AIModel, *, observer: ProviderObserver | None = None) -> None:
        self._model = model
        self._observer = observer

    async def generate[StructuredValue: BaseModel](
        self,
        output_type: type[StructuredValue],
        *,
        stage: str,
        system_instruction: str,
        user_content: str,
        budget: RuntimeBudgetCounter,
        max_output_tokens: int = 1200,
    ) -> StructuredModelResult[StructuredValue]:
        request = AIRequest(
            system_instruction=system_instruction,
            messages=(AIMessage(role="user", content=user_content),),
            max_output_tokens=max_output_tokens,
        )
        results: list[AIResult] = []
        first = await self._call(request, stage=stage, attempt=1, budget=budget)
        results.append(first)
        try:
            value = self._validate(output_type, first.text)
            return StructuredModelResult(value=value, provider_results=tuple(results), repaired=False)
        except StructuredOutputValidationError as first_error:
            try:
                budget.consume_repair()
            except RuntimeError:
                raise first_error from None

        repair_request = AIRequest(
            system_instruction=(
                system_instruction
                + " Return only one JSON object matching the requested schema. "
                "Do not add markdown, commentary, tools, SQL, calculations, or new facts."
            ),
            messages=(
                AIMessage(role="user", content=user_content),
                AIMessage(
                    role="assistant",
                    content=first.text[:MAX_STRUCTURED_RESPONSE_CHARACTERS],
                ),
                AIMessage(
                    role="user",
                    content=(
                        "The preceding response failed schema validation. Re-emit the same "
                        "answer for the ORIGINAL request in the first user message as one "
                        "valid JSON object. This formatting correction is not a new task."
                    ),
                ),
            ),
            max_output_tokens=max_output_tokens,
        )
        repaired = await self._call(repair_request, stage=stage, attempt=2, budget=budget)
        results.append(repaired)
        return StructuredModelResult(
            value=self._validate(output_type, repaired.text),
            provider_results=tuple(results),
            repaired=True,
        )

    async def _call(
        self,
        request: AIRequest,
        *,
        stage: str,
        attempt: int,
        budget: RuntimeBudgetCounter,
    ) -> AIResult:
        budget.consume_model()
        await self._observe(
            ProviderLifecycleEvent(
                event_name="provider.started",
                stage=stage,
                provider=self._model.provider,
                model_id=self._model.model_id,
                attempt=attempt,
            )
        )
        try:
            async with asyncio.timeout(budget.remaining_seconds):
                result = await self._model.generate(request)
            budget.check_deadline()
        except (TimeoutError, RuntimeBudgetExhausted):
            await self._observe(
                ProviderLifecycleEvent(
                    event_name="provider.failed",
                    stage=stage,
                    provider=self._model.provider,
                    model_id=self._model.model_id,
                    attempt=attempt,
                    error_code="latency_budget_exhausted",
                )
            )
            raise RuntimeBudgetExhausted("latency") from None
        except Exception as error:
            await self._observe(
                ProviderLifecycleEvent(
                    event_name="provider.failed",
                    stage=stage,
                    provider=self._model.provider,
                    model_id=self._model.model_id,
                    attempt=attempt,
                    error_code=getattr(error, "code", "ai_provider_unavailable"),
                )
            )
            raise

        usage = result.usage
        await self._observe(
            ProviderLifecycleEvent(
                event_name="provider.completed",
                stage=stage,
                provider=result.provider,
                model_id=result.model_id,
                attempt=attempt,
                latency_ms=result.latency_ms,
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                cached_input_tokens=usage.cached_input_tokens if usage else 0,
                provider_request_id=result.provider_request_id,
                finish_reason=result.finish_reason,
            )
        )
        return result

    async def _observe(self, event: ProviderLifecycleEvent) -> None:
        if self._observer is not None:
            await self._observer(event)

    @staticmethod
    def _validate[StructuredValue: BaseModel](
        output_type: type[StructuredValue],
        text: str,
    ) -> StructuredValue:
        if len(text) > MAX_STRUCTURED_RESPONSE_CHARACTERS:
            raise StructuredOutputValidationError("structured response exceeded the size limit")
        try:
            decoded = json.loads(text)
            if not isinstance(decoded, dict):
                raise TypeError
            return output_type.model_validate(decoded)
        except (json.JSONDecodeError, TypeError, ValidationError, ValueError):
            raise StructuredOutputValidationError(
                "the provider returned malformed structured output"
            ) from None
