from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.core.observability import begin_external_usage, take_external_usage
from app.modules.sources.embedding import (
    EmbeddingUnavailableError,
    HashEmbeddingProvider,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
)
from app.modules.sources.errors import SourceUploadError


def _settings() -> Settings:
    return Settings(openai_api_key="test-key", embedding_provider="openai")


@pytest.mark.asyncio
async def test_openai_requests_768_dimensions_and_orders_response_indexes() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/embeddings"
        body = json.loads(request.content)
        assert body == {
            "model": "text-embedding-3-small",
            "input": ["first", "second"],
            "dimensions": 768,
            "encoding_format": "float",
        }
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "data": [
                    {"index": 1, "embedding": [0.5] * 768},
                    {"index": 0, "embedding": [0.25] * 768},
                ],
            },
        )

    provider = OpenAIEmbeddingProvider(_settings(), transport=httpx.MockTransport(respond))
    assert await provider.embed(["first", "second"]) == [[0.25] * 768, [0.5] * 768]
    assert provider.model_id == "openai:text-embedding-3-small:768:v1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["http", "timeout", "dimensions", "duplicate", "model", "zero", "boolean"]
)
async def test_provider_errors_fail_closed_without_hash_fallback(case: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if case == "timeout":
            raise httpx.ReadTimeout("private provider body", request=request)
        if case == "http":
            return httpx.Response(429, text="private provider body")
        data = [{"index": 0, "embedding": [0.2] * 768}]
        model = "text-embedding-3-small"
        if case == "dimensions":
            data[0]["embedding"] = [0.2] * 767
        elif case == "duplicate":
            data.append(data[0])
        elif case == "model":
            model = "wrong-model"
        elif case == "zero":
            data[0]["embedding"] = [0] * 768
        elif case == "boolean":
            data[0]["embedding"] = [True] * 768
        return httpx.Response(200, json={"model": model, "data": data})

    provider = OpenAIEmbeddingProvider(_settings(), transport=httpx.MockTransport(respond))
    with pytest.raises(EmbeddingUnavailableError) as caught:
        await provider.embed(["private input text"])
    assert "private" not in caught.value.message
    assert caught.value.code == "provider_unavailable"


@pytest.mark.asyncio
async def test_missing_key_never_selects_offline_provider() -> None:
    provider = get_embedding_provider(Settings(embedding_provider="openai"))
    with pytest.raises(EmbeddingUnavailableError):
        await provider.embed(["text"])
    assert isinstance(
        get_embedding_provider(Settings(embedding_provider="hash")), HashEmbeddingProvider
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("texts", [["x" * 8_001], ["x" * 4_000] * 64, ["\ud800"], [" "]])
async def test_embedding_input_limits_reject_before_external_call(texts) -> None:
    def unexpected(request):
        pytest.fail("Rejected input must not reach the provider")

    begin_external_usage()
    provider = OpenAIEmbeddingProvider(_settings(), transport=httpx.MockTransport(unexpected))
    with pytest.raises(SourceUploadError):
        await provider.embed(texts)
    assert take_external_usage().api_calls == 0


@pytest.mark.asyncio
async def test_embedding_response_limit_and_external_call_count() -> None:
    from app.modules.sources.embedding import MAX_EMBEDDING_RESPONSE_BYTES

    def respond(request):
        return httpx.Response(200, content=b" " * (MAX_EMBEDDING_RESPONSE_BYTES + 1))

    begin_external_usage()
    provider = OpenAIEmbeddingProvider(_settings(), transport=httpx.MockTransport(respond))
    with pytest.raises(EmbeddingUnavailableError):
        await provider.embed(["Synthetic source"])
    assert take_external_usage().api_calls == 1
    assert await provider.embed([]) == []
    assert take_external_usage().api_calls == 0
