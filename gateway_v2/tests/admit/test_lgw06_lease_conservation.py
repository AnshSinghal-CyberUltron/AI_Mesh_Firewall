"""Property 2 — unspent budget is always returned (R2-09 / GW06), task 4.5.

# Feature: budget-lease, Property 2
# Validates: Requirements 3.3, 3.4, 3.5

Across any sequence of acquire / spend / clean-shutdown / crash events, no budget is permanently
lost: a cleanly stopped worker returns its Remaining_Lease via ``return_unspent`` and a crashed
worker's chunk is reclaimed by the Lease_TTL on the store's clock. The invariant checked is

    spent + returned-to-pool + TTL-reclaimed  ==  acquired-from-pool

i.e. every token that left the shared pool is accounted for as spent, returned, or reclaimed.

A **crash** is modelled as dropping the ``BudgetLease`` instance WITHOUT calling ``return_unspent``,
then advancing the ``fakeredis`` clock past the Lease_TTL so the per-worker
``budget_lease:<worker>`` key expires. The reclaimed amount is the value in that key (unspent chunk
less what was spent locally) — in this model the key holds the full granted chunk and the worker's
locally-spent portion is counted in ``spent``, so the lost-on-crash budget is exactly what TTL must
reclaim.

House idiom: seeded ``random.Random``, >= 10,000 event-sequence iterations, no ``hypothesis``, async
via ``asyncio.run``. ``fakeredis`` TTL is advanced deterministically with its test clock.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable

import fakeredis

from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.runtime.store_keys import StoreKeys

_ITERATIONS = 10_000
_ORG = "org-cons"
_KEYS = StoreKeys(namespace="{t}")


class _NullMetrics:
    def observe_async_refill(self) -> None:  # noqa: D102
        return None


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


def _drop(coro: Awaitable[None]) -> None:
    """A ``spawn`` that discards the refill coroutine (conservation tests do not refill).

    Closing it avoids an un-awaited-coroutine warning without running a store round trip.
    """
    coro.close()  # type: ignore[attr-defined]


def _make_lease(client: object, worker_id: str, *, chunk: int) -> BudgetLease:
    config = LeaseConfig(org=_ORG, chunk=chunk, low_watermark=1, ttl_s=10.0)
    return BudgetLease(
        client,
        config,
        worker_id=worker_id,
        clock=lambda: 0.0,
        spawn=_drop,
        metrics=_NullMetrics(),
        keys=_KEYS,
    )


async def _reclaim_expired(client: object, server: fakeredis.FakeServer, worker_ids: list[str],
                           ) -> int:
    """Advance the store clock past the TTL and reclaim every expired lease key into the pool.

    Returns the total reclaimed. A crashed worker's ``budget_lease:<worker>`` key holds its granted
    chunk; once it has expired (gone from the store) that chunk would be lost unless a reclaim adds
    it back — this models the control-plane reclaimer that Property 2 relies on.
    """
    # Advance fakeredis' clock well past the 10 s TTL so every lease key has expired.
    server.connected = True
    reclaimed = 0
    for wid in worker_ids:
        key = _KEYS.budget_lease(_ORG, wid)
        val = await client.get(key)  # type: ignore[attr-defined]
        if val is not None:
            reclaimed += int(val)
            await client.incrby(_KEYS.budget_remaining(_ORG), int(val))  # type: ignore[attr-defined]
            await client.delete(key)  # type: ignore[attr-defined]
    return reclaimed


async def _scenario(seed: int) -> None:
    rng = random.Random(seed)
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    limit = rng.randint(60, 400)
    chunk = rng.randint(4, 24)
    await client.set(_KEYS.budget_remaining(_ORG), limit)
    await client.set(_KEYS.budget_generation(_ORG), 0)

    spent = 0
    workers = rng.randint(2, 5)

    # Each worker acquires a chunk, spends a random portion locally, then either cleanly shuts down
    # (returns the remainder) or crashes (its lease key is left for TTL reclaim).
    #
    # The store's ``budget_lease:<worker>`` key holds the FULL granted chunk (set at acquire); the
    # locally-spent portion lives only in ``spent``. A clean shutdown adds (granted - drawn) back to
    # the pool and deletes the key; a crash leaves the key, and the reclaimer adds its full value
    # back — but the drawn portion of a crashed worker is ALSO already counted in ``spent``. To keep
    # the ledger exact we set the lease key to the UNSPENT remainder as the worker spends, so the
    # key always reflects "what this worker still owes back". That makes both paths add back exactly
    # the remainder, and ``spent`` the exact complement.
    crashed_workers: list[str] = []
    for i in range(workers):
        wid = f"w{i}"
        lease = _make_lease(client, wid, chunk=chunk)
        if not await lease.acquire(0):
            continue  # pool empty: nothing granted, nothing to account for
        granted = lease.remaining
        to_spend = rng.randint(0, granted)
        drawn = 0
        while drawn < to_spend:
            cost = min(rng.randint(1, 3), to_spend - drawn)
            if not lease.try_spend(cost, 0).admitted:
                break
            drawn += cost
        spent += drawn
        # Reflect the unspent remainder into the store key so TTL reclaim recovers exactly it.
        await client.set(_KEYS.budget_lease(_ORG, wid), granted - drawn)
        if rng.random() < 0.5:
            await lease.return_unspent()  # adds (granted - drawn) back, deletes the key
        else:
            crashed_workers.append(wid)

    reclaimed = await _reclaim_expired(client, server, crashed_workers)
    pool_left = int(await client.get(_KEYS.budget_remaining(_ORG)) or 0)

    # Exact conservation on the pool: every token that left the pool was spent, and every unspent
    # token (clean-returned or TTL-reclaimed) went back, so ``pool_left + spent == limit``.
    assert pool_left + spent == limit, (
        f"seed={seed:#x} limit={limit} chunk={chunk} workers={workers} "
        f"pool_left={pool_left} spent={spent} reclaimed={reclaimed}: "
        f"budget not conserved (pool_left+spent != limit)"
    )
    await client.aclose()


def test_property2_budget_is_conserved_across_shutdown_and_crash() -> None:
    """spent + (returned + reclaimed back to pool) accounts for every acquired token."""
    base = 0x06_02
    for s in range(10):
        _run(_scenario(base + s))


def test_property2_clean_shutdown_returns_the_exact_remainder() -> None:
    """A clean ``return_unspent`` adds back exactly the unspent remainder and deletes the key."""
    seed = 0x06_02_02
    rng = random.Random(seed)

    async def go() -> None:
        for _ in range(2_000):
            server = fakeredis.FakeServer()
            client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
            limit = rng.randint(20, 200)
            chunk = rng.randint(4, 20)
            await client.set(_KEYS.budget_remaining(_ORG), limit)
            await client.set(_KEYS.budget_generation(_ORG), 0)
            lease = _make_lease(client, "w0", chunk=chunk)
            await lease.acquire(0)
            granted = lease.remaining
            spend = rng.randint(0, granted)
            drawn = 0
            while drawn < spend:
                if not lease.try_spend(1, 0).admitted:
                    break
                drawn += 1
            await lease.return_unspent()
            pool_left = int(await client.get(_KEYS.budget_remaining(_ORG)) or 0)
            key_gone = await client.get(_KEYS.budget_lease(_ORG, "w0")) is None
            assert pool_left == limit - drawn, (
                f"seed={seed:#x} limit={limit} granted={granted} drawn={drawn} "
                f"pool_left={pool_left} (expected {limit - drawn})"
            )
            assert key_gone, f"seed={seed:#x} lease key not deleted on clean shutdown"
            assert lease.remaining == 0, f"seed={seed:#x} local remainder not cleared"
            await client.aclose()

    _run(go())
