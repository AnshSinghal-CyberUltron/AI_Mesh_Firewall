"""GW05c phase 3c — per-key invalidation, single-flight, negative cache.

Two guards carry the card here:

* `test_a_key_write_evicts_only_that_key` — RC2 dropped every principal on every worker on any
  key write. That is the "any key write empties every worker's identity cache" line in R2-02.
* `test_concurrent_cold_misses_share_one_fetch` — RC2 issued one store read per request, which
  is what turned the eviction into 640 x 503 at 25,000 tenants.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import pytest

from gateway_v2.admit.identity import IdentityCache, principal_of
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.state import SignedRecord, StateKind, StateOp, StoreDataUnavailable, Version
from gateway_v2.runtime.state_sig import make_record

SECRET = b"gw05c-identity-test-secret"


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _key_record(
    key_hash: str,
    *,
    org_id: str = "org-a",
    seq: int = 1,
    feed_seq: int = 1,
    deleted: bool = False,
    body: dict[str, object] | None = None,
) -> SignedRecord:
    payload: dict[str, object] = {
        "key_id": f"id-{key_hash}",
        "org_id": org_id,
        "rate_per_s": 10.0,
        "burst": 20.0,
    }
    return make_record(
        SECRET,
        StateKind.KEY,
        key_hash,
        payload if body is None else body,
        Version(1, seq),
        feed_seq,
        deleted=deleted,
        op=StateOp.REVOKE if deleted else StateOp.PUT,
    )


def _principal(key_hash: str, *, org_id: str = "org-a", feed_seq: int = 1) -> Principal:
    return Principal(
        key_id=f"id-{key_hash}",
        org_id=org_id,
        rate_per_s=10.0,
        burst=20.0,
        feed_seq=feed_seq,
    )


class CountingFetch:
    """Counts store reads, and can be held open to force concurrency."""

    def __init__(self, *, gate: asyncio.Event | None = None, missing: bool = False) -> None:
        self.calls: list[str] = []
        self._gate = gate
        self._missing = missing

    async def __call__(self, key_hash: str) -> Principal | None:
        self.calls.append(key_hash)
        if self._gate is not None:
            await self._gate.wait()
        return None if self._missing else _principal(key_hash)


# --- per-key invalidation ----------------------------------------------------------------------


def test_a_key_write_evicts_only_that_key() -> None:
    """RC2 dropped the whole cache here. The bound on revocation is unchanged."""
    cache = IdentityCache(clock=lambda: 0.0)
    cache.mark_applied(5)
    for key_hash in ("a", "b", "c"):
        cache._admit(key_hash, _principal(key_hash, feed_seq=5))

    evicted = cache.on_delta([_key_record("b", seq=6, feed_seq=6)], 6)

    assert evicted == ("b",)
    assert cache.cached("b") is None
    assert cache.cached("a") is not None, "an unrelated key survives a key write"
    assert cache.cached("c") is not None
    assert cache.stats().held == 2


def test_a_revocation_evicts_the_revoked_key() -> None:
    cache = IdentityCache(clock=lambda: 0.0)
    cache.mark_applied(1)
    cache._admit("a", _principal("a", feed_seq=1))

    cache.on_delta([_key_record("a", seq=2, feed_seq=2, deleted=True)], 2)

    assert cache.cached("a") is None


def test_a_key_delta_clears_a_negative_entry() -> None:
    """Otherwise a key_add would not take effect for a key someone probed a moment earlier."""
    cache = IdentityCache(clock=lambda: 0.0, negative_ttl_s=60.0)
    fetch = CountingFetch(missing=True)
    assert _run(cache.resolve("new", fetch)) is None
    assert cache.denied("new") is True

    cache.on_delta([_key_record("new", seq=2, feed_seq=2)], 2)

    assert cache.denied("new") is False


def test_a_non_key_record_in_a_key_delta_is_unavailable() -> None:
    cache = IdentityCache()
    record = make_record(SECRET, StateKind.PLAN, "org-a", {"on": True}, Version(1, 1), 1)

    with pytest.raises(StoreDataUnavailable, match="is not a key"):
        cache.on_delta([record], 1)


# --- single flight -----------------------------------------------------------------------------


def test_concurrent_cold_misses_share_one_fetch() -> None:
    async def scenario() -> tuple[list[str], list[Principal | None], int]:
        gate = asyncio.Event()
        fetch = CountingFetch(gate=gate)
        cache = IdentityCache(clock=lambda: 0.0)
        cache.mark_applied(1)
        waiters = [asyncio.create_task(cache.resolve("hot", fetch)) for _ in range(50)]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*waiters)
        return fetch.calls, list(results), cache.stats().single_flight_joins

    calls, results, joins = _run(scenario())

    assert calls == ["hot"], "50 concurrent callers, ONE store read"
    assert all(item is not None for item in results)
    assert len({item.key_id for item in results if item is not None}) == 1
    assert joins == 49


def test_a_cancelled_caller_does_not_cancel_the_shared_fetch() -> None:
    async def scenario() -> tuple[list[str], Principal | None]:
        gate = asyncio.Event()
        fetch = CountingFetch(gate=gate)
        cache = IdentityCache(clock=lambda: 0.0)
        cache.mark_applied(1)
        first = asyncio.create_task(cache.resolve("hot", fetch))
        second = asyncio.create_task(cache.resolve("hot", fetch))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        gate.set()
        return fetch.calls, await second

    calls, survived = _run(scenario())

    assert calls == ["hot"]
    assert survived is not None, "the caller that stayed still got its principal"


def test_a_failed_fetch_is_not_cached_and_is_retried() -> None:
    async def scenario() -> int:
        attempts = 0

        async def flaky(key_hash: str) -> Principal | None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("store timeout")
            return _principal(key_hash)

        cache = IdentityCache(clock=lambda: 0.0)
        cache.mark_applied(1)
        with pytest.raises(TimeoutError):
            await cache.resolve("hot", flaky)
        assert cache.cached("hot") is None
        assert await cache.resolve("hot", flaky) is not None
        return attempts

    assert _run(scenario()) == 2


def test_a_cache_hit_makes_no_store_read() -> None:
    async def scenario() -> list[str]:
        fetch = CountingFetch()
        cache = IdentityCache(clock=lambda: 0.0)
        cache.mark_applied(1)
        await cache.resolve("hot", fetch)
        await cache.resolve("hot", fetch)
        await cache.resolve("hot", fetch)
        return fetch.calls

    assert _run(scenario()) == ["hot"]


# --- negative cache ----------------------------------------------------------------------------


def test_a_never_issued_key_is_read_once_until_the_ttl_expires() -> None:
    async def scenario() -> list[str]:
        clock = 0.0

        def now() -> float:
            return clock

        fetch = CountingFetch(missing=True)
        cache = IdentityCache(clock=now, negative_ttl_s=2.0)
        for _ in range(100):
            assert await cache.resolve("scan", fetch) is None
        return fetch.calls

    assert _run(scenario()) == ["scan"], "key scanning costs one read, not one per attempt"


def test_the_negative_entry_expires() -> None:
    async def scenario() -> list[str]:
        moment = [0.0]
        fetch = CountingFetch(missing=True)
        cache = IdentityCache(clock=lambda: moment[0], negative_ttl_s=2.0)
        await cache.resolve("scan", fetch)
        moment[0] = 2.5
        await cache.resolve("scan", fetch)
        return fetch.calls

    assert _run(scenario()) == ["scan", "scan"]


def test_a_zero_ttl_disables_negative_caching() -> None:
    async def scenario() -> list[str]:
        fetch = CountingFetch(missing=True)
        cache = IdentityCache(clock=lambda: 0.0, negative_ttl_s=0.0)
        await cache.resolve("scan", fetch)
        await cache.resolve("scan", fetch)
        return fetch.calls

    assert _run(scenario()) == ["scan", "scan"]


# --- bounds and epoch-checked fills -------------------------------------------------------------


def test_the_cache_is_bounded() -> None:
    cache = IdentityCache(capacity=10, clock=lambda: 0.0)
    cache.mark_applied(1)
    for position in range(100):
        cache._admit(f"k{position}", _principal(f"k{position}", feed_seq=1))

    assert cache.stats().held == 10
    assert cache.cached("k0") is None, "the oldest entry was evicted"
    assert cache.cached("k99") is not None


def test_a_rejected_capacity_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        IdentityCache(capacity=0)


def test_a_principal_from_an_unapplied_position_is_not_cached() -> None:
    """Epoch-checked fill: a lagging replica cannot re-admit a key revoked at a newer position."""
    cache = IdentityCache(clock=lambda: 0.0)
    cache.mark_applied(5)

    cache._admit("a", _principal("a", feed_seq=9))

    assert cache.cached("a") is None


def test_a_principal_at_the_applied_position_is_cached() -> None:
    cache = IdentityCache(clock=lambda: 0.0)
    cache.mark_applied(5)

    cache._admit("a", _principal("a", feed_seq=5))

    assert cache.cached("a") is not None


# --- record resolution ---------------------------------------------------------------------------


def test_a_key_record_resolves_to_a_principal() -> None:
    principal = principal_of(_key_record("abc", org_id="org-z", feed_seq=7))

    assert principal is not None
    assert (principal.org_id, principal.key_id, principal.feed_seq) == ("org-z", "id-abc", 7)


def test_a_revoked_record_resolves_to_nothing() -> None:
    assert principal_of(_key_record("abc", deleted=True)) is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"key_id": "", "org_id": "o", "rate_per_s": 1, "burst": 1},
        {"key_id": "k", "org_id": "o", "rate_per_s": "fast", "burst": 1},
        {"key_id": "k", "org_id": "o", "rate_per_s": -1, "burst": 1},
        {"key_id": "k", "org_id": "o", "rate_per_s": True, "burst": 1},
        {"key_id": "k", "rate_per_s": 1, "burst": 1},
    ],
)
def test_a_malformed_key_body_is_unavailable(body: dict[str, object]) -> None:
    with pytest.raises(StoreDataUnavailable):
        principal_of(_key_record("abc", body=body))


def test_a_non_key_record_is_unavailable() -> None:
    record = make_record(SECRET, StateKind.KS, "global", {"on": True}, Version(1, 1), 1)

    with pytest.raises(StoreDataUnavailable, match="is not a key"):
        principal_of(record)
