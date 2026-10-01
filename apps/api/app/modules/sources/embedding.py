from __future__ import annotations

import hashlib
import math
import re
from itertools import pairwise

EMBEDDING_DIMENSIONS = 768
EMBEDDING_MODEL_ID = "carbonmesh-hash-768-v1"

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
