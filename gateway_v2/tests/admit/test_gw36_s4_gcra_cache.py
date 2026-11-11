"""Example-based unit tests for the per-principal GCRA cache (GW36 S4, task 2.2).

Covers the pure ``gateway_v2.admit.gcra_cache.GcraCache`` (no I/O, no store, no event loop):

* **Build-and-cache on first use** (Req 3.2): the first ``for_principal`` for a key builds a
  ``LocalGCRA`` and a second call with the SAME principal returns the SAME instance (identity).
* **Rebuild on changed params** (Req 3.2 / 3.3): a principal whose ``rate_per_s`` OR ``burst``
  changed rebuilds a NEW instance reflecting the new params.
* **Invalidate drops the entry** (Req 3.3): after ``invalidate`` the next ``for_principal``
  rebuilds a fresh instance.
* **Rate 0 = no per-key limit** (Req 3.5): ``rate_per_s == 0`` returns ``None`` and caches the
  ``None`` so a repeat returns ``None`` without constructing a ``LocalGCRA``.
* **Per-worker rate split** (Req 3.4, example): the built GCRA's ``params.rate_per_s`` equals
  ``principal.rate_per_s / worker_count``.
* **Fail closed at construction**: ``worker_count < 1`` raises ``ValueError``.

House idiom: plain pytest functions, ``from __future__ import annotations``, no ``hypothesis``;
example-based here (the property tests are tasks 2.3 / 2.4). A deterministic injected clock is
threaded where a GCRA is built. Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import math

import pytest

from gateway_v2.admit.gcra import LocalGCRA
from gateway_v2.admit.gcra_cache import GcraCache
from gateway_v2.domain.identity import Principal


def _clock() -> float:
    """Deterministic injected clock threaded into every built ``LocalGCRA``."""
    return 0.0


def _principal(
    *,
    rate_per_s: float,
    burst: float,
    key_id: str = "key-a",
    org_id: str = "org-a",
    feed_seq: int = 1,
) -> Principal:
    """Build a minimal valid ``Principal`` via its real constructor, varying only quota fields."""
    return Principal(
        key_id=key_id,
        org_id=org_id,
        rate_per_s=rate_per_s,
        burst=burst,
        feed_seq=feed_seq,
    )


# --------------------------------------------------------------------------- #
# Req 3.2 — build and cache on first use; same instance for an unchanged principal
# --------------------------------------------------------------------------- #


def test_first_use_builds_and_caches_a_gcra() -> None:
    """The first ``for_principal`` for a key builds and returns a ``LocalGCRA`` (Req 3.2)."""
    cache = GcraCache(worker_count=1, clock=_clock)
    gcra = cache.for_principal("hash-1", _principal(rate_per_s=10.0, burst=5))
    assert isinstance(gcra, LocalGCRA)


def test_repeat_unchanged_principal_returns_same_instance() -> None:
    """A second call with the SAME ``(rate_per_s, burst)`` returns the cached instance (Req 3.2)."""
    cache = GcraCache(worker_count=1, clock=_clock)
    first = cache.for_principal("hash-1", _principal(rate_per_s=10.0, burst=5))
    # A freshly built, equal-valued principal object must still resolve the SAME cached GCRA.
    second = cache.for_principal("hash-1", _principal(rate_per_s=10.0, burst=5))
    assert first is second


# --------------------------------------------------------------------------- #
# Req 3.2 / 3.3 — a changed principal rebuilds a new instance reflecting new params
# --------------------------------------------------------------------------- #


def test_changed_rate_rebuilds_new_instance() -> None:
    """A changed ``rate_per_s`` rebuilds a NEW GCRA reflecting the new rate (Req 3.2 / 3.3)."""
    cache = GcraCache(worker_count=1, clock=_clock)
    first = cache.for_principal("hash-1", _principal(rate_per_s=10.0, burst=5))
    second = cache.for_principal("hash-1", _principal(rate_per_s=20.0, burst=5))
    assert second is not first
    assert isinstance(second, LocalGCRA)
    assert math.isclose(second.params.rate_per_s, 20.0)


def test_changed_burst_rebuilds_new_instance() -> None:
    """A changed ``burst`` rebuilds a NEW GCRA reflecting the new burst (Req 3.2 / 3.3)."""
    cache = GcraCache(worker_count=1, clock=_clock)
    first = cache.for_principal("hash-1", _principal(rate_per_s=10.0, burst=5))
    second = cache.for_principal("hash-1", _principal(rate_per_s=10.0, burst=9))
    assert second is not first
    assert isinstance(second, LocalGCRA)
    assert second.params.burst == 9


# --------------------------------------------------------------------------- #
# Req 3.5 — rate 0 returns None (no per-key limit) and caches the None
# --------------------------------------------------------------------------- #


def test_rate_zero_returns_none() -> None:
    """``rate_per_s == 0`` returns ``None`` — no per-key limit (Req 3.5)."""
    cache = GcraCache(worker_count=1, clock=_clock)
    assert cache.for_principal("hash-1", _principal(rate_per_s=0.0, burst=5)) is None


def test_rate_zero_caches_none_and_repeats_none() -> None:
    """The cached ``None`` is returned on a repeat, no ``LocalGCRA`` constructed (Req 3.5)."""
    cache = GcraCache(worker_count=1, clock=_clock)
    first = cache.for_principal("hash-1", _principal(rate_per_s=0.0, burst=5))
    second = cache.for_principal("hash-1", _principal(rate_per_s=0.0, burst=5))
    assert first is None
    assert second is None


# --------------------------------------------------------------------------- #
# Req 3.3 — invalidate drops the entry so the next for_principal rebuilds fresh
# --------------------------------------------------------------------------- #


def test_invalidate_rebuilds_fresh_instance() -> None:
    """``invalidate([hash])`` drops the entry so the next call rebuilds a new object (Req 3.3)."""
    cache = GcraCache(worker_count=1, clock=_clock)
    first = cache.for_principal("hash-1", _principal(rate_per_s=10.0, burst=5))
    cache.invalidate(["hash-1"])
    second = cache.for_principal("hash-1", _principal(rate_per_s=10.0, burst=5))
    assert second is not first
    assert isinstance(second, LocalGCRA)


def test_invalidate_unknown_hash_is_a_noop() -> None:
    """Invalidating a hash that was never cached leaves existing entries intact (Req 3.3)."""
    cache = GcraCache(worker_count=1, clock=_clock)
    first = cache.for_principal("hash-1", _principal(rate_per_s=10.0, burst=5))
    cache.invalidate(["hash-absent"])
    second = cache.for_principal("hash-1", _principal(rate_per_s=10.0, burst=5))
    assert second is first


# --------------------------------------------------------------------------- #
# Req 3.4 — per-worker rate split: built params.rate_per_s == rate / worker_count
# --------------------------------------------------------------------------- #


def test_per_worker_rate_split() -> None:
    """With ``worker_count=N`` the built GCRA's rate is ``principal.rate_per_s / N`` (Req 3.4)."""
    cache = GcraCache(worker_count=4, clock=_clock)
    gcra = cache.for_principal("hash-1", _principal(rate_per_s=40.0, burst=5))
    assert isinstance(gcra, LocalGCRA)
    assert math.isclose(gcra.params.rate_per_s, 40.0 / 4)
    assert gcra.params.burst == 5


# --------------------------------------------------------------------------- #
# Fail closed at construction
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("worker_count", [0, -1])
def test_worker_count_below_one_raises(worker_count: int) -> None:
    """``worker_count < 1`` is rejected at construction so the rate split is well-defined."""
    with pytest.raises(ValueError, match="worker_count"):
        GcraCache(worker_count=worker_count, clock=_clock)
