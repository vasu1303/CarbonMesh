from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Sequence
from itertools import pairwise
from typing import Protocol

import httpx

from app.core.config import Settings, get_settings
from app.core.observability import record_external_call, traced_operation
from app.modules.sources.errors import SourceUploadError

EMBEDDING_DIMENSIONS = 768
EMBEDDING_MODEL_ID = "carbonmesh-hash-768-v1"
MAX_EMBEDDING_INPUT_BYTES = 8_000
MAX_EMBEDDING_BATCH_BYTES = 250_000
MAX_EMBEDDING_RESPONSE_BYTES = 4 * 1024 * 1024

_TOKEN_PATTERN = re.compile(r"\b\w+\b", flags=re.UNICODE)


def _features(text: str) -> list[str]:
    tokens = _TOKEN_PATTERN.findall(text.casefold())
    unigrams = [f"u:{token}" for token in tokens]
    bigrams = [f"b:{left}\x1f{right}" for left, right in pairwise(tokens)]
    return [*unigrams, *bigrams]


def hash_embedding(text: str) -> list[float]:
    """Return a deterministic L2-normalized signed feature-hash embedding."""

    vector = [0.0] * EMBEDDING_DIMENSIONS
    for feature in _features(text):
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], byteorder="big") % EMBEDDING_DIMENSIONS
        sign = 1.0 if digest[8] & 1 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


class EmbeddingUnavailableError(SourceUploadError):
    code = "provider_unavailable"
    status_code = 503
    retryable = True


class EmbeddingProvider(Protocol):
    model_id: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashEmbeddingProvider:
    """Explicit offline/test provider; never selected as a failure fallback."""

    model_id = EMBEDDING_MODEL_ID

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [hash_embedding(text) for text in texts]


class OpenAIEmbeddingProvider:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.model = settings.embedding_model
        self.model_id = f"openai:{self.model}:{EMBEDDING_DIMENSIONS}:v1"
        self._key = settings.openai_api_key
        self._timeout = settings.embedding_timeout_seconds
        self._transport = transport

    @traced_operation("provider.embedding.http")
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._key is None:
            raise EmbeddingUnavailableError("The embedding provider is not configured.")
        try:
            input_sizes = [len(text.encode("utf-8")) for text in texts]
        except UnicodeError as error:
            raise SourceUploadError("Embedding input must be valid UTF-8.", status_code=422) from error
        # UTF-8 byte counts conservatively bound token counts without adding a tokenizer.
        if (
            len(texts) > 128
            or sum(input_sizes) > MAX_EMBEDDING_BATCH_BYTES
            or any(not text.strip() or size > MAX_EMBEDDING_INPUT_BYTES
                   for text, size in zip(texts, input_sizes, strict=True))
        ):
            raise SourceUploadError(
                "Embedding input exceeds the supported bounds.", status_code=422
            )
        try:
            async with asyncio.timeout(self._timeout), httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                record_external_call("embedding")
                async with client.stream(
                    "POST",
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                    json={
                        "model": self.model,
                        "input": list(texts),
                        "dimensions": EMBEDDING_DIMENSIONS,
                        "encoding_format": "float",
                    },
                ) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for part in response.aiter_bytes(chunk_size=64 * 1024):
                        if len(content) + len(part) > MAX_EMBEDDING_RESPONSE_BYTES:
                            raise ValueError("Embedding response exceeds supported size")
                        content.extend(part)
                    payload = json.loads(content)
            if not isinstance(payload, dict) or payload.get("model") != self.model:
                raise ValueError("Embedding model mismatch")
            rows = payload["data"]
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise ValueError("Embedding count mismatch")
            indexed: dict[int, list[float]] = {}
            for row in rows:
                index, vector = row["index"], row["embedding"]
                if type(index) is not int or index not in range(len(texts)) or index in indexed:
                    raise ValueError("Embedding index mismatch")
                if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSIONS:
                    raise ValueError("Embedding dimension mismatch")
                if any(
                    type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1
                    for value in vector
                ):
                    raise ValueError("Invalid embedding values")
                if sum(value * value for value in vector) <= 0:
                    raise ValueError("Empty embedding vector")
                indexed[index] = [float(value) for value in vector]
            return [indexed[index] for index in range(len(texts))]
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as error:
            # Never expose provider bodies, headers, input text, or credentials.
            raise EmbeddingUnavailableError(
                "The embedding provider could not complete the request."
            ) from error


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    if settings.embedding_provider == "hash":
        return HashEmbeddingProvider()
    return OpenAIEmbeddingProvider(settings)


def selected_embedding_model() -> str:
    return get_embedding_provider().model_id
