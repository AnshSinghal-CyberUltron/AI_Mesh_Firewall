"""GW31 query/ingest embedding seam and a model-free deterministic embedder (dev and tests)."""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

__all__ = ("DeterministicHashEmbedder", "Embedder")

_WORD = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    @property
    def dimension(self) -> int: ...

    async def embed(
        self, org_id: str, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...] | None:
        """``None`` means unavailable."""
        ...


class DeterministicHashEmbedder:
    """Feature hashing of lowercased word tokens, L2 normalised. No model, no network."""

    def __init__(self, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("embedding dimension must be >= 1")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(
        self, org_id: str, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...] | None:
        del org_id
        return tuple(self._one(text) for text in texts)

    def _one(self, text: str) -> tuple[float, ...]:
        vec = [0.0] * self._dimension
        for token in _WORD.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimension
            vec[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            vec[0] = 1.0
            return tuple(vec)
        return tuple(x / norm for x in vec)
