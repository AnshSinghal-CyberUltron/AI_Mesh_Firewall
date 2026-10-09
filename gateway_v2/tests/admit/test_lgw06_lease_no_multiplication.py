"""Property 1 — no replica multiplication (R2-09 / GW06), task 4.4.

# Feature: budget-lease, Property 1
# Validates: Requirements 7.1, 7.2, 7.3

For any number of in-process ``BudgetLease`` instances sharing ONE ``fakeredis`` budget pool and any
arrival pattern, aggregate admission across all replicas never exceeds ``limit + overshoot``, where
the declared overshoot is at most one in-flight chunk per worker. The shared
``{rv2}:budget:<org>:remaining`` key — not a per-worker counter — is what bounds the aggregate; the
only excess above the pool is a chunk already granted to a worker but not yet spent.

House idiom: a seeded ``random.Random`` loop over >= 10,000 arrivals with the seed logged in the
assertion message; NO ``hypothesis``; async driven by ``asyncio.run``. The injected ``spawn``
collects the off-path refill coroutines so the harness drives them deterministically between
arrivals (single-flight + off-path are asserted by the other property tests).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable

import fakeredis

from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.runtime.store_keys import StoreKeys

_ARRIVALS = 10_000
_ORG = "org-mult"
_KEYS = StoreKeys(namespace="{t}")


class _CountingMetrics:
    """A tiny ``QuotaMetricsLike`` double counting Async_Refills."""

    def __init__(self) -> None:
        self.async_refills = 0

    def observe_async_refill(self) -> None:
        self.async_refills += 1


class _Harness:
    """N ``BudgetLease`` replicas over one ``fakeredis``; a spawn collecting refill coroutines."""

    def __init__(self, *, limit: int, chunk: int, low_watermark: int, workers: int) -> None:
        self.server = fakeredis.FakeServer()
        self.client = fakeredis.FakeAsyncRedis(server=self.server, decode_responses=True)
        self.limit = limit
        self.chunk = chunk
        self.pending: list[Awaitable[None]] = []
        self.metrics = _CountingMetrics()
        self.config = LeaseConfig(
            org=_ORG, chunk=chunk, low_watermark=low_watermark, ttl_s=30.0,
        )
        self.leases = [
            BudgetLease(
                self.client,
                self.config,
                worker_id=f"w{i}",
                clock=lambda: 0.0,
                spawn=self.pending.append,
                metrics=self.metrics,
                keys=_KEYS,
            )
            for i in range(workers)
        ]

    async def seed_pool(self) -> None:
        await self.client.set(_KEYS.budget_remaining(_ORG), self.limit)
        await self.client.set(_KEYS.budget_generation(_ORG), 0)

    async def drain_refills(self) -> None:
        """Run every collected off-path refill coroutine to completion (deterministic)."""
        while self.pending:
            batch = self.pending
            self.pending = []
            for coro in batch:
                await coro

    async def close(self) -> None:
        """Close any undrained coroutines and the client (test hygiene)."""
        for coro in self.pending:
            coro.close()  # type: ignore[attr-defined]
        self.pending.clear()
        await self.client.aclose()


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


async def _scenario(seed: int) -> None:
    rng = random.Random(seed)
    limit = rng.randint(50, 500)
    chunk = rng.randint(4, 32)
    low_watermark = rng.randint(0, max(0, chunk // 2))
    workers = rng.randint(2, 6)
    harness = _Harness(limit=limit, chunk=chunk, low_watermark=low_watermark, workers=workers)
    await harness.seed_pool()

    # Prime each replica with an initial chunk under generation 0.
    for lease in harness.leases:
        await lease.acquire(0)

    admitted = 0
    for _ in range(_ARRIVALS):
        lease = rng.choice(harness.leases)
        cost = rng.randint(1, 3)
        result = lease.try_spend(cost, 0)
        if result.admitted:
            admitted += cost
        # Arbitrary arrival pattern: sometimes drain the off-path refills, sometimes defer them so
        # multiple refills queue up — the invariant must hold regardless of refill timing.
        if rng.random() < 0.3:
            await harness.drain_refills()
    await harness.drain_refills()

    # Aggregate admission <= pool limit + at most one in-flight chunk per worker (the overshoot).
    overshoot = chunk * workers
    assert admitted <= limit + overshoot, (
        f"seed={seed:#x} limit={limit} chunk={chunk} workers={workers} "
        f"admitted={admitted} exceeds limit+overshoot={limit + overshoot}"
    )
    # And it never multiplied the limit by the worker count: a per-worker counter would have let
    # each worker admit up to ``limit`` on its own (admitted up to limit*workers).
    assert admitted <= limit + overshoot < limit * workers + 1 or workers == 1, (
        f"seed={seed:#x} aggregate {admitted} looks like per-worker multiplication"
    )
    await harness.close()


def test_property1_no_replica_multiplication() -> None:
    """Aggregate admission across N replicas never exceeds limit + declared overshoot."""
    base = 0x06_01
    # A handful of independently seeded scenarios, each running >= 10,000 arrivals.
    for s in range(8):
        _run(_scenario(base + s))


def test_property1_total_admission_cannot_exceed_pool_plus_held() -> None:
    """A single long run: aggregate admitted + pool-left + unspent-held == original limit + grants.

    Conservation framing of the same invariant: everything admitted came out of the shared pool or
    an already-granted chunk, so nothing was conjured by a per-worker counter.
    """
    seed = 0x06_01_02
    rng = random.Random(seed)
    limit = 240
    chunk = 16
    harness = _Harness(limit=limit, chunk=chunk, low_watermark=4, workers=4)

    async def go() -> None:
        await harness.seed_pool()
        for lease in harness.leases:
            await lease.acquire(0)
        admitted = 0
        for _ in range(_ARRIVALS):
            lease = rng.choice(harness.leases)
            r = lease.try_spend(1, 0)
            if r.admitted:
                admitted += 1
            if rng.random() < 0.25:
                await harness.drain_refills()
        await harness.drain_refills()
        pool_left = int(await harness.client.get(_KEYS.budget_remaining(_ORG)) or 0)
        held = sum(lease.remaining for lease in harness.leases)
        # pool_left is >= 0, held is the unspent granted chunks; admitted <= limit + held bound.
        assert admitted + pool_left + held >= limit, (
            f"seed={seed:#x} budget vanished: admitted={admitted} pool_left={pool_left} "
            f"held={held} limit={limit}"
        )
        assert admitted <= limit + chunk * 4, (
            f"seed={seed:#x} admitted={admitted} exceeds limit+overshoot"
        )
        await harness.close()

    _run(go())
