"""Property 4 — budget outage is narrow (R2-09 / GW06), task 7.1.

# Feature: budget-lease, Property 4
# Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6

During a store outage the :class:`~gateway_v2.admit.quota.QuotaComponent` spends only the
Remaining_Lease and then refuses **only quota** with ``posture.BUDGET_UNAVAILABLE`` — it never
returns ``shared_state_unavailable`` (that is identity/plan's global posture, owned by other
components, cross-referenced not here). The façade reaches no store on the request path, so the
outage is invisible to it until the local lease runs dry.

The outage is simulated by partitioning the ``fakeredis`` client: every store command raises
``ConnectionError`` once the partition is up, so any attempted refill ``acquire`` fails off the
request path and the Remaining_Lease is all the worker has. The façade must drain exactly the
pre-acquired Remaining_Lease (admitting those) and refuse every subsequent request narrowly.

House idiom: seeded ``random.Random``, >= 10,000 iterations, no ``hypothesis``, async via
``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable
from typing import Any

import fakeredis
from redis.exceptions import ConnectionError as RedisConnectionError

from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.grant import Admitted, BudgetVerdict
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.quota import QuotaComponent
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE, SHARED_STATE_UNAVAILABLE
from gateway_v2.runtime.store_keys import StoreKeys

_ITERATIONS = 10_000
_ORG = "org-outage"
_KEYS = StoreKeys(namespace="{t}")


class _PartitionableClient:
    """A ``fakeredis`` async client that can be flipped into a partition (every op raises).

    While ``partitioned`` is set, every store command and every pipeline raises
    ``ConnectionError`` — the fail-closed store outage. The façade must never surface this on the
    request path (it issues no store call there); only an off-path refill ``acquire`` can hit it,
    and the lease's ``_refill`` swallows the failure and keeps serving from the Remaining_Lease.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.partitioned = False

    def pipeline(self, *args: Any, **kwargs: Any) -> Any:
        if self.partitioned:
            raise RedisConnectionError("partitioned (pipeline)")
        return self._inner.pipeline(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if callable(attr):
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                if self.partitioned:
                    raise RedisConnectionError(f"partitioned ({name})")
                return attr(*args, **kwargs)
            return wrapped
        return attr


class _CountingMetrics:
    """A ``QuotaMetricsLike`` + budget_unavailable observer double."""

    def __init__(self) -> None:
        self.async_refills = 0
        self.budget_unavailable = 0

    def observe_async_refill(self) -> None:
        self.async_refills += 1

    def observe_budget_unavailable(self) -> None:
        self.budget_unavailable += 1


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


def _drop(coro: Awaitable[None]) -> None:
    """A ``spawn`` that discards the refill coroutine (it would fail under partition anyway)."""
    # Under partition the refill coroutine raises inside acquire; the lease's _refill catches it in
    # a finally and clears the guard. We close it synchronously here (no event loop re-entry) — the
    # off-path refill never affects the Remaining_Lease during an outage, which is the point.
    coro.close()  # type: ignore[attr-defined]


async def _one_outage(seed: int) -> None:
    rng = random.Random(seed)
    server = fakeredis.FakeServer()
    inner = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    client = _PartitionableClient(inner)
    metrics = _CountingMetrics()

    chunk = rng.randint(4, 40)
    low = rng.randint(0, max(0, chunk // 2))
    config = LeaseConfig(org=_ORG, chunk=chunk, low_watermark=low, ttl_s=30.0)
    await inner.set(_KEYS.budget_remaining(_ORG), 10_000)
    await inner.set(_KEYS.budget_generation(_ORG), 0)
    lease = BudgetLease(
        client,
        config,
        worker_id="w0",
        clock=lambda: 0.0,
        spawn=_drop,
        metrics=metrics,
        keys=_KEYS,
    )
    await lease.acquire(0)  # pre-acquire a chunk while healthy
    held = lease.remaining
    assert held > 0

    # A GCRA generous enough that it never limits here (we are testing the budget posture, not
    # burst). High rate + large burst so every arrival clears the local check.
    gcra = LocalGCRA(GcraParams(rate_per_s=1_000_000.0, burst=100_000), clock=lambda: 0.0)
    quota = QuotaComponent(gcra, lease, generation_source=lambda: 0, metrics=metrics)

    # Partition the store: no refill can land, so the worker has exactly ``held`` budget.
    client.partitioned = True

    admitted = 0
    refused = 0
    # Try more requests than the Remaining_Lease can serve so we cross into the refusal regime.
    for n in range(held + rng.randint(5, 50)):
        verdict = await quota.evaluate(1, request_id=f"req-{seed:#x}-{n}")
        if isinstance(verdict, Admitted):
            admitted += 1
        else:
            assert isinstance(verdict, BudgetVerdict), f"seed={seed:#x} non-verdict {verdict!r}"
            # The narrow posture — NEVER the global one (Req 5.3).
            assert verdict.code == BUDGET_UNAVAILABLE, (
                f"seed={seed:#x} refusal code {verdict.code!r} != budget_unavailable"
            )
            assert verdict.code != SHARED_STATE_UNAVAILABLE
            assert verdict.should_retry is False
            assert verdict.retry_after_s >= 1.0, (
                f"seed={seed:#x} retry_after {verdict.retry_after_s} below MIN_RETRY_AFTER_S"
            )
            refused += 1

    # Spent exactly the Remaining_Lease (the pre-acquired chunk), then refused the rest.
    assert admitted == held, (
        f"seed={seed:#x} admitted={admitted} != pre-acquired Remaining_Lease={held}"
    )
    assert refused >= 1, f"seed={seed:#x} never crossed into the refusal regime"
    assert metrics.budget_unavailable == refused, (
        f"seed={seed:#x} budget_unavailable metric {metrics.budget_unavailable} != {refused}"
    )
    await inner.aclose()


def test_property4_outage_spends_remaining_then_budget_unavailable() -> None:
    """During a partition: spend the Remaining_Lease, then budget_unavailable (never global)."""
    base = 0x06_04
    for s in range(8):
        _run(_one_outage(base + s))


def test_property4_long_run_posture_is_always_narrow() -> None:
    """A long single run under partition never emits ``shared_state_unavailable``."""
    seed = 0x06_04_02

    async def go() -> None:
        rng = random.Random(seed)
        server = fakeredis.FakeServer()
        inner = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        client = _PartitionableClient(inner)
        metrics = _CountingMetrics()
        config = LeaseConfig(org=_ORG, chunk=16, low_watermark=4, ttl_s=30.0)
        await inner.set(_KEYS.budget_remaining(_ORG), 1_000)
        await inner.set(_KEYS.budget_generation(_ORG), 0)
        lease = BudgetLease(
            client, config, worker_id="w0", clock=lambda: 0.0,
            spawn=_drop, metrics=metrics, keys=_KEYS,
        )
        await lease.acquire(0)
        held = lease.remaining
        gcra = LocalGCRA(GcraParams(rate_per_s=1_000_000.0, burst=100_000), clock=lambda: 0.0)
        quota = QuotaComponent(gcra, lease, generation_source=lambda: 0, metrics=metrics)
        client.partitioned = True

        admitted = 0
        for n in range(_ITERATIONS):
            verdict = await quota.evaluate(rng.randint(1, 2), request_id=f"r{n}")
            if isinstance(verdict, Admitted):
                admitted += 1
            else:
                assert verdict.code == BUDGET_UNAVAILABLE, (
                    f"seed={seed:#x} iter={n} code {verdict.code!r} is not the narrow posture"
                )
                assert verdict.code != SHARED_STATE_UNAVAILABLE
        # Admitted no more than the held budget (cost >= 1 each), then refused the rest narrowly.
        assert admitted <= held, f"seed={seed:#x} admitted {admitted} exceeds held {held}"
        await inner.aclose()

    _run(go())
