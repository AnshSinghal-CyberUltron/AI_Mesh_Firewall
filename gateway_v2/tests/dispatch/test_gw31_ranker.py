"""GW31 ranker properties (P4 order/determinism, P5 trust, P6 duplicate cap)."""

from __future__ import annotations

import math
import random

from gateway_v2.dispatch.rag_ranker import cosine, rank
from gateway_v2.domain.rag import Chunk, ScoredChunk, TrustLevel
from gateway_v2.runtime.resources_rag import RagBounds

B = RagBounds()
Q = (1.0, 0.0, 0.0, 0.0)


def _chunk(
    cid: str, vec: tuple[float, ...], trust: TrustLevel = TrustLevel.MEDIUM, text: str = ""
) -> ScoredChunk:
    return ScoredChunk(Chunk(cid, text or f"text {cid}", vec, "o", "p", "s", trust), 0.0)


def _unit(rng: random.Random, dim: int = 4) -> tuple[float, ...]:
    v = [rng.gauss(0, 1) for _ in range(dim)]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / n for x in v)


# Feature: gw31-rag-pipeline, Property 4: ranker is deterministic, bounded and threshold-respecting
def test_property_deterministic_bounded_threshold() -> None:
    rng = random.Random(4)
    for _ in range(10_000):
        n = rng.randint(0, 12)
        chunks = [
            _chunk(f"c{i}", _unit(rng), rng.choice(list(TrustLevel)), f"t{rng.random()}")
            for i in range(n)
        ]
        thr = rng.random()
        cap = rng.randint(1, 8)
        a = rank(Q, tuple(chunks), threshold=thr, max_documents=cap, bounds=B)
        shuffled = chunks[:]
        rng.shuffle(shuffled)
        b = rank(Q, tuple(shuffled), threshold=thr, max_documents=cap, bounds=B)
        assert [c.chunk.chunk_id for c in a.chunks] == [c.chunk.chunk_id for c in b.chunks]
        assert len(a.chunks) <= cap
        assert all(c.relevance >= thr for c in a.chunks)


# Feature: gw31-rag-pipeline, Property 5: lower trust never outranks equal higher trust
def test_property_trust_primary() -> None:
    rng = random.Random(5)
    for _ in range(10_000):
        vec = _unit(rng)
        hi = _chunk("hi", vec, TrustLevel.MEDIUM, "alpha beta gamma delta epsilon zeta one")
        lo = _chunk(
            "lo",
            vec,
            rng.choice([TrustLevel.UNTRUSTED, TrustLevel.LOW]),
            "completely different words about other things entirely",
        )
        out = rank(Q, (lo, hi), threshold=0.0, max_documents=5, bounds=B)
        ids = [c.chunk.chunk_id for c in out.chunks]
        assert ids == sorted(ids, key=lambda i: 0 if i == "hi" else 1)


# Feature: gw31-rag-pipeline, Property 6: near-duplicate share is capped on the post-cap context
def test_property_duplicate_cap_post_cap() -> None:
    rng = random.Random(6)
    for _ in range(1000):
        flood = rng.randint(2, 12)
        others = rng.randint(0, 4)
        vec = (0.9, 0.1, 0.0, 0.0)
        chunks = [
            _chunk(
                f"d{i}", vec, TrustLevel.LOW, "same flood text repeated again and again for sure"
            )
            for i in range(flood)
        ]
        for j in range(others):
            chunks.append(
                _chunk(
                    f"o{j}",
                    (0.5, 0.5, float(j + 1), 0.0),
                    TrustLevel.MEDIUM,
                    f"unique content number {j} {rng.random()}",
                )
            )
        out = rank(Q, tuple(chunks), threshold=0.0, max_documents=50, bounds=B)
        kept_dups = sum(1 for c in out.chunks if c.chunk.chunk_id.startswith("d"))
        assert kept_dups >= 1
        assert kept_dups <= max(1, math.floor(B.near_duplicate_fraction * len(out.chunks)))


def test_flood_of_ten_beside_two_keeps_two() -> None:
    chunks = [
        _chunk(
            f"d{i}", (0.9, 0.1, 0, 0), TrustLevel.LOW, "same flood text repeated again and again"
        )
        for i in range(10)
    ]
    chunks += [
        _chunk("o1", (0.6, 0.0, 0.8, 0), text="first unique piece of content here today"),
        _chunk("o2", (0.6, 0.0, 0.0, 0.8), text="second unrelated paragraph about trains"),
    ]
    out = rank(Q, tuple(chunks), threshold=0.0, max_documents=50, bounds=B)
    assert sum(1 for c in out.chunks if c.chunk.chunk_id.startswith("d")) == 2


def test_unscorable_dropped_and_counted() -> None:
    bad = [
        _chunk("a", ()),
        _chunk("b", (0.0, 0.0, 0.0, 0.0)),
        _chunk("c", (1.0, 0.0)),
        _chunk("ok", Q),
    ]
    out = rank(Q, tuple(bad), threshold=0.0, max_documents=5, bounds=B)
    assert [c.chunk.chunk_id for c in out.chunks] == ["ok"]
    assert out.dropped_unscorable == 3


def test_threshold_never_lowered() -> None:
    out = rank(Q, (_chunk("x", (-1.0, 0.0, 0.0, 0.0)),), threshold=0.6, max_documents=5, bounds=B)
    assert out.chunks == () and out.dropped_threshold == 1


def test_cosine_rejects_non_finite() -> None:
    assert cosine((1.0, float("nan")), (1.0, 1.0)) is None
