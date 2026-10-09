"""Property 3 — stale generation is never spent (R2-09 / GW06), task 4.6.

# Feature: budget-lease, Property 3
# Validates: Requirements 6.1, 6.2, 6.3, 6.4, 13.4

A ``BudgetLease`` carrying a Budget_Generation older than the current generation is never spent:
``try_spend`` returns a ``stale_generation`` result (admitting nothing), and only after a re-acquire
under the current generation does budget admission resume — and it then draws exclusively from the
lease acquired under the new generation. The generation is advanced mid-stream to exercise the
revoke-on-change path (Req 6.2).

House idiom: seeded ``random.Random``, >= 10,000 iterations, no ``hypothesis``, async via
``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable

import fakeredis

from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.runtime.store_keys import StoreKeys

_ITERATIONS = 10_000
_ORG = "org-gen"
_KEYS = StoreKeys(namespace="{t}")


class _NullMetrics:
    def observe_async_refill(self) -> None:  # noqa: D102
        return None


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


def _drop(coro: Awaitable[None]) -> None:
    """A ``spawn`` that discards the refill coroutine (these tests drive acquire directly)."""
    coro.close()  # type: ignore[attr-defined]


def _lease(client: object, *, chunk: int) -> BudgetLease:
    config = LeaseConfig(org=_ORG, chunk=chunk, low_watermark=0, ttl_s=30.0)
    return BudgetLease(
        client,
        config,
        worker_id="w0",
        clock=lambda: 0.0,
        spawn=_drop,
        metrics=_NullMetrics(),
        keys=_KEYS,
    )


def test_property3_stale_generation_is_never_spent() -> None:
    """A ``try_spend`` under a newer generation than the held lease admits nothing (stale)."""
    seed = 0x06_03
    rng = random.Random(seed)

    async def go() -> None:
        for i in range(_ITERATIONS):
            server = fakeredis.FakeServer()
            client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
            chunk = rng.randint(4, 20)
            await client.set(_KEYS.budget_remaining(_ORG), 1_000)
            await client.set(_KEYS.budget_generation(_ORG), 0)
            lease = _lease(client, chunk=chunk)
            await lease.acquire(0)
            assert lease.remaining > 0

            # Advance the org's budget generation (config change): the held lease (gen 0) is now
            # stale against generation 1.
            new_gen = rng.randint(1, 5)
            await client.set(_KEYS.budget_generation(_ORG), new_gen)

            held_before = lease.remaining
            result = lease.try_spend(1, new_gen)
            assert result.stale_generation is True, (
                f"seed={seed:#x} iter={i} stale lease not flagged: {result}"
            )
            assert result.admitted is False, (
                f"seed={seed:#x} iter={i} spent from a stale-generation lease"
            )
            assert lease.remaining == held_before, (
                f"seed={seed:#x} iter={i} stale try_spend mutated the Remaining_Lease"
            )
            await client.aclose()

    _run(go())


def test_property3_reacquire_under_current_generation_before_admitting() -> None:
    """After a generation change, a re-acquire under the new gen happens before budget admission."""
    seed = 0x06_03_02
    rng = random.Random(seed)

    async def go() -> None:
        for i in range(_ITERATIONS):
            server = fakeredis.FakeServer()
            client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
            chunk = rng.randint(4, 20)
            await client.set(_KEYS.budget_remaining(_ORG), 1_000)
            await client.set(_KEYS.budget_generation(_ORG), 0)
            lease = _lease(client, chunk=chunk)
            await lease.acquire(0)

            new_gen = rng.randint(1, 4)
            await client.set(_KEYS.budget_generation(_ORG), new_gen)

            # Stale: no admission.
            assert lease.try_spend(1, new_gen).stale_generation is True

            # Re-acquire under the current generation (the caller's response to a stale result).
            acquired = await lease.acquire(new_gen)
            assert acquired is True, f"seed={seed:#x} iter={i} re-acquire under new gen failed"
            assert lease.generation == new_gen, (
                f"seed={seed:#x} iter={i} lease did not adopt the new generation"
            )

            # Now admission resumes and draws from the lease acquired under the new generation.
            result = lease.try_spend(1, new_gen)
            assert result.admitted is True and result.stale_generation is False, (
                f"seed={seed:#x} iter={i} admission did not resume under the current gen: {result}"
            )
            await client.aclose()

    _run(go())


def test_property3_acquire_self_corrects_on_stale_generation() -> None:
    """``acquire`` under a stale generation adopts the store's current gen and re-acquires once."""
    seed = 0x06_03_03
    rng = random.Random(seed)

    async def go() -> None:
        for i in range(2_000):
            server = fakeredis.FakeServer()
            client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
            chunk = rng.randint(4, 20)
            await client.set(_KEYS.budget_remaining(_ORG), 500)
            current = rng.randint(1, 7)
            await client.set(_KEYS.budget_generation(_ORG), current)
            lease = _lease(client, chunk=chunk)

            # Ask to acquire under a STALE generation (0 < current): acquire must self-correct to
            # the store's generation and still obtain a chunk under it.
            acquired = await lease.acquire(0)
            assert acquired is True, f"seed={seed:#x} iter={i} acquire did not self-correct"
            assert lease.generation == current, (
                f"seed={seed:#x} iter={i} acquire adopted gen {lease.generation}, want {current}"
            )
            assert lease.remaining > 0
            await client.aclose()

    _run(go())
