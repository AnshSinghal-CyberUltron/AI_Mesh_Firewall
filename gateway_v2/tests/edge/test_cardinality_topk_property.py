# Feature: observability-contract, Property 3: Top-K preserves the largest magnitudes
# Validates: Requirements 3.1, 3.4, 3.5, 3.6
"""Property 3 — Top-K retains the K largest magnitudes + conservation holds.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

For any multiset of per-tenant magnitudes, the K individual series retained are exactly the
K-largest under the deterministic tie-break ``(magnitude desc, tenant_key asc)``; the ``other``
series equals the sum of all remaining magnitudes; the sum of exposed per-tenant values plus
``other`` equals the total observed (conservation); and repeated exposition of the same input set
yields the SAME selection (determinism).
"""

from __future__ import annotations

import random

from gateway_v2.edge.cardinality import CardinalityDirectory

_SEED = 0x0B5E_0003
_ITERATIONS = 10_000


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _reference_top_k(slots: dict[str, int], k: int) -> list[tuple[str, int]]:
    """The K-largest under ``(mag desc, key asc)``, computed independently of the directory."""
    ordered = sorted(slots.items(), key=lambda kv: (-kv[1], kv[0]))
    return ordered[:k]


def test_property_3_top_k_preserves_largest_and_conserves() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        capacity = rng.randint(1, 20)
        k = rng.randint(1, capacity)
        directory = CardinalityDirectory(_capacity=capacity, _k=k)

        # Observe only up to capacity distinct keys (overflow is Property 1's concern); here we
        # want a populated directory to exercise selection + conservation. Deliberately include
        # ties by drawing magnitudes from a small range.
        n_keys = rng.randint(0, capacity)
        keys = [f"tenant_{j:03d}" for j in range(n_keys)]
        rng.shuffle(keys)
        slots: dict[str, int] = {}
        for key in keys:
            magnitude = rng.randint(0, 8)  # small range => frequent ties at the K-th boundary
            directory.observe(key, magnitude)
            slots[key] = magnitude

        top_k = directory.top_k_series()
        reference = _reference_top_k(slots, k)

        # Retained set == K-largest under the documented tie-break, same order (R3.1/R3.6).
        assert top_k == reference, _ctx(
            _SEED, i, note="top_k != reference", got=top_k, want=reference
        )

        # At most K retained; when <= K tenants observed, every observed tenant is retained (R3.4).
        assert len(top_k) <= k, _ctx(_SEED, i, note="more than K retained", n=len(top_k), k=k)
        if n_keys <= k:
            assert len(top_k) == n_keys, _ctx(
                _SEED, i, note="under-K: not every tenant retained", n=len(top_k), keys=n_keys
            )

        # other == sum of the rest (R3.5), and never negative.
        retained_keys = {key for key, _ in top_k}
        expected_other = sum(mag for key, mag in slots.items() if key not in retained_keys)
        assert directory.other_value() == expected_other, _ctx(
            _SEED, i, note="other mismatch", got=directory.other_value(), want=expected_other
        )
        assert directory.other_value() >= 0, _ctx(_SEED, i, note="negative other")

        # Conservation: sum(top-K) + other == sum(all slots) (Property 3 invariant).
        total = sum(slots.values())
        exposed = sum(mag for _key, mag in top_k) + directory.other_value()
        assert exposed == total, _ctx(
            _SEED, i, note="conservation broken", exposed=exposed, total=total
        )

        # Determinism: a second read of the same state yields the identical selection (R3.6).
        assert directory.top_k_series() == top_k, _ctx(
            _SEED, i, note="selection not stable across reads"
        )
