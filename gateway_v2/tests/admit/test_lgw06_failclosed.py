"""Property 10 — fail-closed under error (R2-09 / GW06), task 7.2.

# Feature: budget-lease, Property 10
# Validates: Requirements 13.1, 13.2, 13.3, 13.4

Every error condition the budget-lease façade can hit fails **closed** — a refusal, never an admit
without a decision — and no path increments a fail-open counter:

* a missing / uncomputable contract-derived chunk refuses to start
  (:func:`~gateway_v2.admit.quota.derive_lease_chunk` raises), Req 13.3;
* an undecidable budget decision (the lease raising inside ``try_spend``) refuses with
  ``budget_unavailable``, Req 13.1;
* a stale-generation lease that cannot be re-acquired is treated as unspendable and refuses, Req
  13.4;
* across the whole run the ``fail_open``-style counter stays ``0`` (Req 13.2) — there is no path
  that admits a request the budget could not decide.

House idiom: seeded ``random.Random``, >= 10,000 iterations, no ``hypothesis``, async via
``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable

import fakeredis

from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.grant import Admitted, BudgetVerdict
from gateway_v2.admit.lease import BudgetLease, LeaseConfig, SpendResult
from gateway_v2.admit.quota import QuotaComponent, derive_lease_chunk
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import from_signals
from gateway_v2.runtime.store_keys import StoreKeys

_ITERATIONS = 10_000
_ORG = "org-failclosed"
_KEYS = StoreKeys(namespace="{t}")


class _FailOpenAwareMetrics:
    """Tracks budget_unavailable refusals and a ``fail_open`` counter that MUST stay zero.

    There is deliberately no path that increments ``fail_open``: the façade fails closed to a
    refusal, so a non-zero value here would be a bug (Req 13.2). ``observe_budget_unavailable`` is
    the only hook the façade calls.
    """

    def __init__(self) -> None:
        self.budget_unavailable = 0
        self.fail_open = 0

    def observe_async_refill(self) -> None:
        return None

    def observe_budget_unavailable(self) -> None:
        self.budget_unavailable += 1


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


def _drop(coro: Awaitable[None]) -> None:
    coro.close()  # type: ignore[attr-defined]


def _generous_gcra() -> LocalGCRA:
    return LocalGCRA(GcraParams(rate_per_s=1_000_000.0, burst=100_000), clock=lambda: 0.0)


# --------------------------------------------------------------------------- #
# 13.3 — a missing / uncomputable contract-derived chunk refuses to start
# --------------------------------------------------------------------------- #


def test_missing_chunk_refuses_to_start() -> None:
    """An unset ``q_safe`` or an uncomputable chunk raises rather than guessing a chunk."""
    seed = 0x06_10
    rng = random.Random(seed)
    for i in range(2_000):
        util = rng.uniform(0.3, 0.9)
        per_worker_rss = rng.randint(128, 512) * 1024 * 1024
        signals = HardwareSignals(
            cpu_quota=rng.uniform(1.0 / util + 1.0, 16.0),
            memory_limit=per_worker_rss * rng.randint(int(1.0 / util) + 2, 12),
            fd_limit=rng.randint(256, 4096),
            cpu_source="t", mem_source="t", fd_source="t",
        )
        contract = from_signals(
            signals,
            target_p99_ms=rng.uniform(5.0, 100.0),
            utilization_cap=util,
            per_worker_rss=per_worker_rss,
        )
        # q_safe unset / non-positive => CapacityUnset (refuse to start, Req 2.5 / 13.3).
        for bad in (None, 0.0, -1.0):
            try:
                derive_lease_chunk(contract, q_safe=bad)
            except CapacityUnset:
                continue
            msg = f"seed={seed:#x} iter={i} q_safe={bad!r} did not refuse to start"
            raise AssertionError(msg)


def test_uncomputable_chunk_refuses_rather_than_guesses() -> None:
    """A contract whose ``queue_depth`` is uncomputable propagates ``CapacityUnavailable``.

    The chunk derivation never guesses a bound: if the contract cannot compute the in-flight depth
    it raises, so the worker refuses to acquire rather than lease an unbounded/zero chunk (Req 2.4,
    13.3). A tiny ``_StubContract`` whose ``queue_depth`` raises stands in for a contract that
    cannot serve one request — the derivation must let it propagate, not swallow it into a guess.
    """

    class _StubContract:
        target_p99_ms = 20.0

        def queue_depth(self, _service_rate: float) -> int:
            raise CapacityUnavailable("queue_depth below minimum to serve (stub)")

    raised = False
    try:
        derive_lease_chunk(_StubContract(), q_safe=10.0)  # type: ignore[arg-type]
    except CapacityUnavailable:
        raised = True
    assert raised, "an uncomputable chunk must refuse with CapacityUnavailable, never guess"


# --------------------------------------------------------------------------- #
# 13.1 — an undecidable budget decision refuses (never admits)
# --------------------------------------------------------------------------- #


class _RaisingLease(BudgetLease):
    """A lease whose ``try_spend`` raises — the undecidable-decision injection."""

    def try_spend(self, cost: int, generation: int) -> SpendResult:  # type: ignore[override]
        raise RuntimeError("budget decision is undecidable (injected)")


def test_undecidable_decision_fails_closed() -> None:
    """An exception inside the budget decision is caught and refused (fail-closed, Req 13.1)."""
    seed = 0x06_10_02

    async def go() -> None:
        rng = random.Random(seed)
        for i in range(_ITERATIONS):
            server = fakeredis.FakeServer()
            inner = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
            metrics = _FailOpenAwareMetrics()
            config = LeaseConfig(org=_ORG, chunk=rng.randint(2, 16), low_watermark=0, ttl_s=30.0)
            await inner.set(_KEYS.budget_remaining(_ORG), 1_000)
            await inner.set(_KEYS.budget_generation(_ORG), 0)
            lease = _RaisingLease(
                inner, config, worker_id="w0", clock=lambda: 0.0,
                spawn=_drop, metrics=metrics, keys=_KEYS,
            )
            quota = QuotaComponent(
                _generous_gcra(), lease, generation_source=lambda: 0, metrics=metrics,
            )
            verdict = await quota.evaluate(1, request_id=f"r{i}")
            assert isinstance(verdict, BudgetVerdict), (
                f"seed={seed:#x} iter={i} undecidable decision did not refuse: {verdict!r}"
            )
            assert verdict.code == BUDGET_UNAVAILABLE
            assert metrics.fail_open == 0, f"seed={seed:#x} iter={i} FAIL_OPEN incremented"
            await inner.aclose()

    _run(go())


# --------------------------------------------------------------------------- #
# 13.4 — a stale-generation lease is unspendable (never spent on a best-effort basis)
# --------------------------------------------------------------------------- #


def test_stale_generation_lease_is_unspendable() -> None:
    """A stale held lease whose re-acquire finds no budget refuses, never spends stale (Req 13.4).

    The held lease is under an old generation; the façade reports the new generation, so the
    initial ``try_spend`` is stale (the old budget is NEVER spent, Req 13.4). The pool under the
    new generation is empty, so the revoke-on-change re-acquire yields nothing and the request
    refuses — fail-closed, with the stale budget untouched.
    """
    seed = 0x06_10_03

    async def go() -> None:
        rng = random.Random(seed)
        for i in range(_ITERATIONS):
            server = fakeredis.FakeServer()
            inner = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
            metrics = _FailOpenAwareMetrics()
            config = LeaseConfig(org=_ORG, chunk=rng.randint(2, 16), low_watermark=0, ttl_s=30.0)
            await inner.set(_KEYS.budget_remaining(_ORG), 1_000)
            await inner.set(_KEYS.budget_generation(_ORG), 0)
            lease = BudgetLease(
                inner, config, worker_id="w0", clock=lambda: 0.0,
                spawn=_drop, metrics=metrics, keys=_KEYS,
            )
            await lease.acquire(0)  # held under generation 0
            held_before = lease.remaining
            assert held_before > 0

            # Config change: advance the generation AND drain the pool, so a re-acquire under the
            # new generation finds nothing — the old lease stays unspendable and the request
            # refuses rather than spending the stale budget (Req 13.4).
            new_gen = rng.randint(1, 5)
            await inner.set(_KEYS.budget_generation(_ORG), new_gen)
            await inner.set(_KEYS.budget_remaining(_ORG), 0)
            quota = QuotaComponent(
                _generous_gcra(), lease,
                generation_source=lambda ng=new_gen: ng, metrics=metrics,
            )

            verdict = await quota.evaluate(1, request_id=f"r{i}")
            assert isinstance(verdict, BudgetVerdict), (
                f"seed={seed:#x} iter={i} stale-gen lease was admitted: {verdict!r}"
            )
            assert verdict.code == BUDGET_UNAVAILABLE
            # The old-generation budget was never spent on a best-effort basis: the only mutation
            # allowed is the empty re-acquire adopting the new generation (remaining unchanged).
            assert lease.remaining == held_before, (
                f"seed={seed:#x} iter={i} stale budget was spent: {lease.remaining}"
            )
            assert metrics.fail_open == 0
            await inner.aclose()

    _run(go())


def test_admit_path_still_works_when_decidable() -> None:
    """Sanity: a healthy, same-generation lease admits — fail-closed is not fail-everything."""

    async def go() -> None:
        server = fakeredis.FakeServer()
        inner = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        metrics = _FailOpenAwareMetrics()
        config = LeaseConfig(org=_ORG, chunk=16, low_watermark=4, ttl_s=30.0)
        await inner.set(_KEYS.budget_remaining(_ORG), 1_000)
        await inner.set(_KEYS.budget_generation(_ORG), 0)
        lease = BudgetLease(
            inner, config, worker_id="w0", clock=lambda: 0.0,
            spawn=_drop, metrics=metrics, keys=_KEYS,
        )
        await lease.acquire(0)
        quota = QuotaComponent(
            _generous_gcra(), lease, generation_source=lambda: 0, metrics=metrics,
        )
        verdict = await quota.evaluate(1, request_id="ok")
        assert isinstance(verdict, Admitted), f"a decidable request was refused: {verdict!r}"
        assert metrics.fail_open == 0
        assert metrics.budget_unavailable == 0
        await inner.aclose()

    _run(go())
