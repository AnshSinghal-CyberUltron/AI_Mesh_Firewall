"""GW31 ranker: relevance, threshold, distance anomaly, near-duplicate cap, trust re-rank.

Pure and deterministic. Ties break by ``(-relevance, -trust, chunk_id)``, never by arrival order
or hash seed. Every numeric bound arrives through :class:`RagBounds`; there is no literal here.
No detector, no resolver, no HTTP. A model is never called.
"""
from __future__ import annotations

import dataclasses
import math
import re
from dataclasses import dataclass

from gateway_v2.domain.rag import ScoredChunk
from gateway_v2.runtime.resources_rag import RagBounds

__all__ = ("RankOutcome", "cosine", "jaccard", "rank", "relevance_of", "shingles")

_WORD = re.compile(r"[a-z0-9]+")
_MAD_SCALE = 1.4826


@dataclass(frozen=True, slots=True)
class RankOutcome:
    chunks: tuple[ScoredChunk, ...]
    dropped_blocked: int = 0
    dropped_anomalous: int = 0
    dropped_duplicate: int = 0
    dropped_threshold: int = 0
    dropped_unscorable: int = 0


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float | None:
    """``None`` for a length mismatch, an empty or zero-norm vector, or a non-finite value."""
    if not a or len(a) != len(b):
        return None
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=True):
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return None
    value = dot / (math.sqrt(na) * math.sqrt(nb))
    return max(-1.0, min(1.0, value))


def relevance_of(query: tuple[float, ...], vector: tuple[float, ...]) -> float | None:
    """``(cosine + 1) / 2`` clamped to [0, 1]; orthogonal vectors score about 0.5."""
    cos = cosine(query, vector)
    if cos is None:
        return None
    return max(0.0, min(1.0, (cos + 1.0) / 2.0))


def shingles(text: str, width: int) -> frozenset[tuple[str, ...]]:
    words = _WORD.findall(text.lower())
    if len(words) <= width:
        return frozenset({tuple(words)})
    return frozenset(tuple(words[i : i + width]) for i in range(len(words) - width + 1))


def jaccard(a: frozenset[tuple[str, ...]], b: frozenset[tuple[str, ...]]) -> float:
    union = len(a | b)
    return 1.0 if union == 0 else len(a & b) / union


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _key(item: ScoredChunk) -> tuple[int, float, str]:
    return (-int(item.chunk.trust), -item.relevance, item.chunk.chunk_id)


def _anomalous(items: list[ScoredChunk], bounds: RagBounds) -> set[str]:
    if len(items) < bounds.anomaly_min_set:
        return set()
    dist = {i.chunk.chunk_id: 1.0 - i.relevance for i in items}
    median = _median(list(dist.values()))
    mad = _median([abs(d - median) for d in dist.values()])
    out: set[str] = set()
    for cid, d in dist.items():
        if mad > 0:
            if (d - median) / (_MAD_SCALE * mad) > bounds.anomaly_z:
                out.add(cid)
        elif d - median > bounds.anomaly_floor:
            out.add(cid)
    return out


def _near_duplicate_cap(items: list[ScoredChunk], bounds: RagBounds) -> list[ScoredChunk]:
    ordered = sorted(items, key=_key)
    sh = {i.chunk.chunk_id: shingles(i.chunk.text, bounds.shingle_width) for i in ordered}
    clusters: list[list[ScoredChunk]] = []
    for item in ordered:
        for cluster in clusters:
            if any(_similar(item, m, sh, bounds) for m in cluster):
                cluster.append(item)
                break
        else:
            clusters.append([item])
    kept = [len(c) for c in clusters]
    while True:
        bound = max(1, math.floor(bounds.near_duplicate_fraction * sum(kept)))
        lowered = [min(k, bound) for k in kept]
        if lowered == kept:
            break
        kept = lowered
    out: list[ScoredChunk] = []
    for cluster, k in zip(clusters, kept, strict=True):
        out.extend(cluster[:k])
    return out


def _similar(
    a: ScoredChunk,
    b: ScoredChunk,
    sh: dict[str, frozenset[tuple[str, ...]]],
    bounds: RagBounds,
) -> bool:
    cos = cosine(a.chunk.vector, b.chunk.vector)
    if cos is not None and cos >= bounds.duplicate_similarity:
        return True
    return jaccard(sh[a.chunk.chunk_id], sh[b.chunk.chunk_id]) >= bounds.duplicate_jaccard


def rank(
    query_vector: tuple[float, ...],
    chunks: tuple[ScoredChunk, ...],
    *,
    threshold: float,
    max_documents: int,
    bounds: RagBounds,
    unscorable_already: int = 0,
) -> RankOutcome:
    """Run every pure ranking step. The threshold is never lowered to fill the context."""
    scored: list[ScoredChunk] = []
    unscorable = unscorable_already
    for item in chunks:
        rel = relevance_of(query_vector, item.chunk.vector)
        if rel is None:
            unscorable += 1
        else:
            scored.append(dataclasses.replace(item, relevance=rel))
    cleared = [i for i in scored if i.relevance >= threshold]
    dropped_threshold = len(scored) - len(cleared)
    bad = _anomalous(cleared, bounds)
    survivors = [i for i in cleared if i.chunk.chunk_id not in bad]
    capped = _near_duplicate_cap(survivors, bounds)
    final = sorted(capped, key=_key)[:max_documents]
    return RankOutcome(
        chunks=tuple(final),
        dropped_anomalous=len(bad),
        dropped_duplicate=len(survivors) - len(capped),
        dropped_threshold=dropped_threshold,
        dropped_unscorable=unscorable,
    )
