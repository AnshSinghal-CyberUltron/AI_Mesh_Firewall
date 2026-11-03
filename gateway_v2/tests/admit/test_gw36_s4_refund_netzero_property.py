"""Property 2 — refund net-zero before a provider call (GW36 S4, task 4.4).

# Feature: gw36-s4-admission-quota, Property 2: Refund net-zero before a provider call
# Validates: Requirements 5.4

For any request that spends a cost against its owner's lease and is then refused *before* any
provider call, applying ``refund(cost, request_id=..., owner=...)`` returns the owner's lease
remaining balance to EXACTLY its pre-spend value — the net budget change for that request is zero.

Harness (design §P2, the cleanest Property-2 construction): one org's REAL
:class:`~gateway_v2.admit.quota.QuotaComponent` over a REAL
:class:`~gateway_v2.admit.lease.BudgetLease` on ``fakeredis`` (the real-lease-over-fakeredis idiom
from ``test_v2_leaks_budget.py`` / ``test_lgw06_lease_conservation.py``). Per round:

1. seed the store pool far above the cost and ``acquire`` one chunk — this is the pre-spend state;
2. record the observable remaining balance (``lease.remaining``, the local Remaining_Lease the
   component spends against, and the store pool ``{rv2}:budget:{org}:remaining`` which ``acquire``
   last wrote);
3. ``await component.evaluate(cost, request_id=...)`` — the spend — and assert it ``Admitted`` (so
   the net-zero below is meaningful: a spend actually happened before the refund);
4. ``component.refund(cost, request_id=...)`` — the refund — and assert the observable remaining
   balance is byte-for-byte back to its pre-spend value, and the store pool key is unchanged.

GCRA is kept out of the way: the component's internal :class:`~gateway_v2.admit.gcra.LocalGCRA` is
built with a very high rate and burst so it admits every request and only the budget round trip is
exercised. The pool is seeded far above the chunk and the Low_Watermark is ``0`` so no Async_Refill
fires during the round (the ``spawn`` is a discarding stub regardless — the benign
"BudgetLease._refill never awaited" note in AGENTS.md is not a failure).

House idiom: seeded ``random.Random``, ``_ITERATIONS = 10_000`` (the house >= 10,000), the seed in
every assertion message, no ``hypothesis``, async spend via ``asyncio.run``; the refund is
synchronous. Generators: arbitrary non-negative ``cost``; a pre-existing remaining balance >=
``cost`` (the pool is seeded so the acquired chunk covers the spend and the spend is admitted); an
arbitrary ``request_id`` and ``owner``.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable

import fakeredis

from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.grant import Admitted
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.quota import QuotaComponent
from gateway_v2.runtime.store_keys import StoreKeys

_ITERATIONS = 10_000
_KEYS = StoreKeys(namespace="{t}")

#: The fixed Budget_Generation the component checks the held lease against. Both ``evaluate`` and
#: ``refund`` read it from the same ``generation_source``, so the lease's generation guard matches
#: on the refund and the credit is applied (not dropped as a cross-generation refund).
_GENERATION = 0


class _NullMetrics:
    """The minimal metrics surface the lease / component produce into (a no-op double)."""

    def observe_async_refill(self) -> None:  # noqa: D102
        return None

    def observe_budget_unavailable(self) -> None:  # noqa: D102
        return None


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


def _drop(coro: Awaitable[None]) -> None:
    """A ``spawn`` that discards the refill coroutine (no refill fires in this property).

    Closing it avoids an un-awaited-coroutine warning without running a store round trip; the
    benign "BudgetLease._refill never awaited" note (AGENTS.md) is not a failure.
    """
    coro.close()  # type: ignore[attr-defined]


def _component(
    client: object, org: str, *, chunk: int
) -> tuple[QuotaComponent, BudgetLease]:
    """A REAL ``QuotaComponent`` over a REAL ``BudgetLease`` on ``client``, GCRA out of the way."""
    lease = BudgetLease(
        client,
        LeaseConfig(org=org, chunk=chunk, low_watermark=0, ttl_s=30.0),
        worker_id="w0",
        clock=lambda: 0.0,
        spawn=_drop,
        metrics=_NullMetrics(),
        keys=_KEYS,
    )
    # A very high rate + burst so the component's internal GCRA admits every arrival: only the
    # budget spend/refund round trip is exercised by this property.
    gcra = LocalGCRA(GcraParams(rate_per_s=1_000_000_000.0, burst=1_000_000_000), clock=lambda: 0.0)
    component = QuotaComponent(
        gcra, lease, generation_source=lambda: _GENERATION, metrics=_NullMetrics()
    )
    return component, lease


async def _round(seed: int) -> None:
    rng = random.Random(seed)

    # Generators: an arbitrary non-negative cost, and a pre-existing remaining balance >= cost. The
    # chunk (what one acquire grants) is seeded >= cost so the spend is admitted; the pool is seeded
    # far above the chunk so acquire grants the full chunk and no refill can fire (watermark 0).
    cost = rng.randint(0, 50_000)
    chunk = cost + rng.randint(1, 50_000)  # strictly > cost, so the acquired lease covers the spend
    pool = chunk * rng.randint(4, 32)  # far above the chunk: acquire grants exactly ``chunk``
    org = f"org-{rng.randint(0, 9)}"
    request_id = f"req-{rng.randrange(1 << 40):x}"
    owner = org  # the owner the spend/refund is attributed to; the component is this org's lease

    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    await client.set(_KEYS.budget_remaining(org), pool)
    await client.set(_KEYS.budget_generation(org), _GENERATION)

    component, lease = _component(client, org, chunk=chunk)
    granted = await lease.acquire(_GENERATION)
    assert granted, f"seed={seed:#x} harness: the first chunk was not granted (pool={pool})"

    # The pre-spend observables: the local Remaining_Lease the component spends against, and the
    # store pool key acquire last wrote (neither evaluate nor refund touches the store here).
    remaining_before = lease.remaining
    pool_key = _KEYS.budget_remaining(org)
    store_pool_before = int(await client.get(pool_key) or 0)

    # The spend MUST happen (and be admitted) before the refund, so the net-zero is meaningful.
    verdict = await component.evaluate(cost, request_id=request_id, owner=owner)
    assert isinstance(verdict, Admitted), (
        f"seed={seed:#x} cost={cost} chunk={chunk} remaining_before={remaining_before}: "
        f"the spend was not admitted ({verdict!r}); the refund net-zero would be vacuous"
    )
    assert verdict.owner_id == org, (
        f"seed={seed:#x} owner={owner!r}: Admitted.owner_id {verdict.owner_id!r} != org {org!r}"
    )
    # After the spend the lease is down by exactly ``cost`` (a non-trivial change to undo).
    assert lease.remaining == remaining_before - cost, (
        f"seed={seed:#x} cost={cost}: remaining {lease.remaining} after spend != "
        f"{remaining_before - cost} (before {remaining_before})"
    )

    # The refund — before any provider call — returns exactly the spent cost (Req 5.4).
    component.refund(cost, request_id=request_id, owner=owner)

    remaining_after = lease.remaining
    store_pool_after = int(await client.get(pool_key) or 0)

    # Net-zero: the observable remaining balance is byte-for-byte back to its pre-spend value.
    assert remaining_after == remaining_before, (
        f"seed={seed:#x} cost={cost} chunk={chunk} owner={owner!r} request_id={request_id!r}: "
        f"net budget change not zero — remaining {remaining_before} before spend, "
        f"{remaining_after} after refund (expected {remaining_before})"
    )
    # The store pool key is unchanged across evaluate+refund (both are local, no store I/O).
    assert store_pool_after == store_pool_before, (
        f"seed={seed:#x} cost={cost}: store pool {store_pool_before} before -> "
        f"{store_pool_after} after; evaluate/refund must not touch {pool_key!r}"
    )

    await client.aclose()


def test_property2_refund_is_net_zero_before_a_provider_call() -> None:
    """A spend followed by its refund (before any provider call) nets exactly zero on the lease."""
    base = 0x_9536_5204  # gw36 s4, property 2
    for offset in range(_ITERATIONS):
        _run(_round(base + offset))
