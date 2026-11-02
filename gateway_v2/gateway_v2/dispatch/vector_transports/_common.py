"""Helpers shared by the real per-store ``Vector_Transport`` clients (GW30)."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from types import MappingProxyType

from gateway_v2.dispatch.vector_adapter import AdapterResult, VectorRecord
from gateway_v2.domain import posture

__all__ = (
    "as_point_id",
    "collection_exists",
    "connection_params_repr",
    "cosine_similarity",
    "parse_pgvector_text",
    "records_from_scored",
    "safe_name",
    "vector_literal",
)


def cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Pure cosine similarity between two equal-length vectors (0.0 on a zero norm)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def vector_literal(vector: tuple[float, ...]) -> str:
    """Render a vector as a pgvector text literal, e.g. ``[0.1,0.2,0.3]``."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def parse_pgvector_text(text: str) -> tuple[float, ...]:
    """Parse a pgvector text literal ``[a,b,c]`` back into a float tuple."""
    stripped = text.strip().strip("[]")
    if not stripped:
        return ()
    return tuple(float(part) for part in stripped.split(","))


def safe_name(collection: str) -> str:
    """Collection name restricted to alnum/underscore, as every store's identifier rules allow."""
    return "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in collection)


def records_from_scored(
    scored: list[tuple[float, str, tuple[float, ...]]],
    top_k: int,
) -> tuple[VectorRecord, ...]:
    """Build the uniform record tuple from (score, id, vector), highest score first.

    One deterministic ordering (descending score, then id) so every connector yields the same
    result-id order for the same input — the L30-1 "identical result sets" contract.
    """
    scored.sort(key=lambda t: (-t[0], t[1]))
    return tuple(
        VectorRecord(id=rid, vector=vec, metadata=MappingProxyType({}))
        for _, rid, vec in scored[:top_k]
    )


def as_point_id(record_id: str) -> str:
    """Map a string record id deterministically to a UUID (Qdrant/Weaviate need int or UUID)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, record_id))


def collection_exists() -> AdapterResult:
    """The declared R2.9 refusal: the collection already exists and was left untouched."""
    return AdapterResult(code=posture.COLLECTION_EXISTS)


def connection_params_repr(params: Mapping[str, object]) -> str:
    """Render connection params with any secret field redacted (defensive helper)."""
    safe = {
        k: ("<redacted>" if "pass" in k.lower() or "secret" in k.lower() else v)
        for k, v in params.items()
    }
    return repr(safe)
