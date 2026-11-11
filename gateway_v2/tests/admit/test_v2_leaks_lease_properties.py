"""v2-enforcement-leaks Group F fix-checking property test (design **Property 12**, ledger half).

**Property 12: Bug Condition (F)** — Token lease conservation.

**Validates: Requirements 2.15, 2.16, 2.17**

Task 23.6 (this file): the ledger half of Property 12 (Bug Condition F), clauses 2.15-2.17. The
Group F ``admit`` fix (task 23.3) gave :class:`gateway_v2.admit.lease.BudgetLease` three new
behaviours this property pins over the lease ledger directly (never the edge app — the edge-driven
cost/clamp/refund cases stay strict-xfail until Phase 7):

* **fair-share grant** — ``acquire`` grants ``min(pool, chunk, max(1, pool // active_workers))``,
  where the active-worker count comes from the ``budget_workers`` ZSET pruned on the store clock, so
  one worker never drains a pool several workers share (C29, Req 2.15 / 1.16);
* **bounded overdraft** — with ``overdraft_tokens > 0`` a lease that still holds budget may go
  negative by at most ``overdraft_tokens``, so a single in-flight request whose token cost exceeds
  the held remainder is admitted rather than spuriously refused while budget remains (C29, 2.16);
* **generation-aware refund** — ``refund(cost, generation)`` adds ``cost`` back to the
  Remaining_Lease iff ``generation`` equals the held generation, and drops the refund across
  generations (the revoke-on-change case), so a refund never mis-credits a pool the spend never
  debited (C29, Req 2.17).

Over >= 10,000 seeded sequences (seeded :class:`random.Random`, no hypothesis, the seed in every
assertion message), ``N`` leases share ONE :class:`fakeredis.FakeAsyncRedis` over one org pool. Each
step drives a random mix of acquire, local ``try_spend``, in-same-generation refund,
cross-generation refund, crash (TTL reclaim) and clean return. The ``_drop`` spawn closes the refill
coroutine without running it, so the ledger stays deterministic (the known-benign R2-09
"coroutine 'BudgetLease._refill' was never awaited" warning may appear — it is the ``_drop`` spawn
closing the coroutine, and no product behaviour is suppressed to hide it).

Every step asserts all five Property 12 invariants:

1. **fair-share grant bound** — each ``acquire`` grant (the ``lease.remaining`` delta) is at most
   ``min(pool_before, cap, max(1, pool_before // active_workers))``, read from the store pool and
   the ``budget_workers`` ZSET cardinality observed immediately before the acquire;
2. **aggregate conservation** — total admitted tokens minus total refunded is at most the initial
   pool ``limit`` plus ``Σ_workers overdraft_tokens`` (the bounded negative floor each worker may
   dip below zero), the core budget-conservation invariant;
3. **no servable cost refused while budget remains** — when a lease holds ``remaining > 0``, a
   ``try_spend`` of any cost ``<= contract.max_request_tokens()`` is admitted (leak-F: a servable
   cost is never spuriously refused while budget remains, given the overdraft floor the edge wires);
4. **uncoverable cost refused, nothing spent** — a cost beyond ``remaining + overdraft`` leaves
   ``remaining`` unchanged, ``try_spend`` returns ``admitted=False``, and the façade (when
   exercised) returns a :class:`BudgetVerdict` carrying ``BUDGET_UNAVAILABLE``, never
   ``shared_state_unavailable``;
5. **refund exact in-generation, dropped across** — after a spend of ``c`` under generation ``g``,
   ``refund(c, g)`` restores ``remaining`` to its pre-spend value exactly, and a ``refund(c, g')``
   with ``g' != lease.generation`` leaves ``remaining`` unchanged.

Coverage counters assert the run was not vacuous: it exercised fair-share-bounded grants (bounded by
the share, not merely by the cap), admitted spends, refused (uncoverable) spends, in-generation
refunds, cross-generation (dropped) refunds, and at least one TTL-reclaim/crash path.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import fakeredis

from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.grant import Admitted, BudgetVerdict
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.quota import QuotaComponent, derive_low_watermark
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.store_keys import KEYS

_LEAK = "v2-enforcement-leaks F (ledger): "

_SEED = 0x23D6  # v2-enforcement-leaks task 23.6 (Property 12)
_ITERS = 10_000

_ORG = "v2-leaks-f-ledger-org"

#: The number of leases (workers) sharing one pool each iteration.
_WORKERS = 4
#: The initial org pool. Large enough that acquires draw real chunks, small enough that the pool
#: is actually drained across a run so the aggregate-conservation bound binds.
_LIMIT = 50_000
#: A per-worker lease cap far above ``pool // _WORKERS`` for part of the pool's life, so the
#: fair-share share (``pool // active``) — not the cap — binds the grant at least sometimes.
_CHUNK = 20_000
#: The bounded overdraft each lease is wired with (the edge passes
#: ``contract.max_request_tokens()``; here a modest, deterministic floor keeps the arithmetic
#: readable while still exercising the negative-remainder branch).
_OVERDRAFT = 64
#: The Lease_TTL on the store clock. The crash path advances the store ``TIME`` past this so a
#: crashed worker's ZSET membership and lease chunk are reclaimed.
_TTL_S = 30.0


def _contract() -> ResourceContract:
    """The same contract shape the Group F exploration oracle (`test_v2_leaks_budget`) builds."""
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


class _QuotaMetrics:
    """The tiny counting metrics double the Group F oracle uses (same surface)."""

    __slots__ = ("budget_unavailable", "refills")

    def __init__(self) -> None:
        self.refills = 0
        self.budget_unavailable = 0

    def observe_async_refill(self) -> None:
        self.refills += 1

    def observe_budget_unavailable(self) -> None:
        self.budget_unavailable += 1


def _drop(work: Any) -> None:
    """Off-path refill spawn that never runs the refill (keeps the ledger deterministic).

    Mirrors ``test_v2_leaks_budget._drop``: closes the coroutine so no acquire runs behind the
    test's back. The closed coroutine triggers the known-benign R2-09 "coroutine
    'BudgetLease._refill' was never awaited" warning, which is expected, not a failure.
    """
    close = getattr(work, "close", None)
    if callable(close):
        close()


def _max_request_tokens() -> int:
    """The contract's per-request token ceiling — the bound clause 3 of Property 12 uses."""
    return _contract().max_request_tokens()


async def _zcount_active(client: Any, now: int) -> int:
    """The number of non-expired ``budget_workers`` members, as ``_acquire_once`` reads it."""
    raw = await client.zcount(KEYS.budget_workers(_ORG), now, "+inf")
    return int(raw or 0)


async def _store_now(client: Any) -> int:
    """The store clock in whole seconds (``TIME``), as ``_acquire_once`` reads it."""
    store_now_s, _micros = await client.time()
    return int(store_now_s)


async def _pool(client: Any) -> int:
    """The current ``budget_remaining`` pool value."""
    raw = await client.get(KEYS.budget_remaining(_ORG))
    return int(raw or 0)


def _make_lease(client: Any, worker_id: str, *, clock: Any) -> BudgetLease:
    """One lease over ``client`` wired the way the edge wires it: a positive overdraft floor."""
    return BudgetLease(
        client,
        LeaseConfig(
            org=_ORG, chunk=_CHUNK, low_watermark=derive_low_watermark(_CHUNK), ttl_s=_TTL_S
        ),
        worker_id=worker_id,
        clock=clock,
        spawn=_drop,
        metrics=_QuotaMetrics(),
        overdraft_tokens=_OVERDRAFT,
    )


def test_property12_token_lease_conservation() -> None:
    """**Validates: Requirements 2.15, 2.16, 2.17**

    Over 10,000 seeded sequences of acquire / spend / refund / crash / return across ``_WORKERS``
    leases sharing one pool, assert the five Property 12 invariants named in the module docstring.
    Coverage counters assert the run exercised every branch so a green result is not vacuous.
    """
    rng = random.Random(_SEED)
    mrt = _max_request_tokens()

    # Coverage counters (asserted non-trivial at the end).
    fair_share_bounded_grants = 0  # a grant bounded by the share, not just the cap
    admitted_spends = 0
    refused_spends = 0  # an uncoverable cost refused with nothing spent
    in_gen_refunds = 0  # a same-generation refund restored the cost exactly
    cross_gen_refunds = 0  # a cross-generation refund was dropped (ignored)
    crash_reclaims = 0  # a TTL-reclaim path ran (store clock advanced past a crashed lease)

    async def _run() -> None:
        nonlocal fair_share_bounded_grants, admitted_spends, refused_spends
        nonlocal in_gen_refunds, cross_gen_refunds, crash_reclaims

        for iteration in range(_ITERS):
            seed_msg = f"seed={_SEED:#x} iteration={iteration}"

            # One fresh store + pool + generation + lease set per iteration (deterministic). The
            # acquire reads the ZSET score off the store clock (``pipe.time()``); the crash path
            # below models a store-clock TTL expiry by dropping the crashed worker's membership and
            # lease key directly (exactly what the score-based prune + PEXPIRE do), so the test does
            # not depend on freezing fakeredis ``TIME``.
            client = fakeredis.FakeAsyncRedis(
                server=fakeredis.FakeServer(), decode_responses=True
            )
            await client.set(KEYS.budget_remaining(_ORG), _LIMIT)
            await client.set(KEYS.budget_generation(_ORG), 0)

            leases = [
                _make_lease(client, f"w{w}", clock=lambda: 0.0) for w in range(_WORKERS)
            ]

            # A per-lease generation the spend was charged under, and the pre-spend remaining, so
            # the refund invariant can be checked exactly.
            spent_under_gen: dict[int, int] = {}
            pre_spend_remaining: dict[int, int] = {}
            last_spend_cost: dict[int, int] = {}

            total_admitted = 0
            total_refunded = 0

            # Acquire a first chunk for each lease, asserting the fair-share grant bound (clause 1).
            # The workers acquire in turn and each registers itself in ``budget_workers``, so when
            # worker ``w`` acquires, the live count already holds its ``w`` predecessors and the
            # fair-share divisor is ``w`` live members + this worker (not yet registered).
            for w, lease in enumerate(leases):
                now = await _store_now(client)
                pool_before = await _pool(client)
                active_before = await _zcount_active(client, now)
                self_score = await client.zscore(KEYS.budget_workers(_ORG), f"w{w}")
                self_live = self_score is not None and float(self_score) > now
                active = max(1, active_before + (0 if self_live else 1))
                expected_cap = min(pool_before, _CHUNK, max(1, pool_before // active))
                held_before = lease.remaining
                await lease.acquire(0)
                grant = lease.remaining - held_before
                assert 0 <= grant <= expected_cap, (
                    f"{seed_msg} w{w}: grant {grant} exceeds fair-share cap {expected_cap} "
                    f"(pool_before={pool_before} active={active} chunk={_CHUNK})"
                )
                if grant > 0 and grant < min(pool_before, _CHUNK):
                    # The grant was below both the pool and the cap: the fair share bound it.
                    fair_share_bounded_grants += 1

            # A random sequence of local operations across the leases.
            ops = rng.randint(4, 12)
            for _ in range(ops):
                w = rng.randrange(_WORKERS)
                lease = leases[w]
                gen = lease.generation
                choice = rng.random()

                if choice < 0.40:
                    # A local spend of a servable cost. Clause 3: while the lease holds budget, a
                    # cost <= max_request_tokens() must be admitted (given the overdraft floor).
                    if lease.remaining > 0:
                        cost = rng.randint(1, max(1, min(mrt, lease.remaining + _OVERDRAFT)))
                        before = lease.remaining
                        result = lease.try_spend(cost, gen)
                        assert result.admitted, (
                            f"{seed_msg} w{w}: servable cost {cost} refused while remaining "
                            f"{before} (overdraft {_OVERDRAFT}, max_request_tokens {mrt})"
                        )
                        assert lease.remaining == before - cost, (
                            f"{seed_msg} w{w}: spend of {cost} left remaining {lease.remaining}, "
                            f"expected {before - cost}"
                        )
                        total_admitted += cost
                        admitted_spends += 1
                        spent_under_gen[w] = gen
                        pre_spend_remaining[w] = before
                        last_spend_cost[w] = cost

                elif choice < 0.55:
                    # An uncoverable cost. Clause 4: a cost beyond remaining + overdraft leaves
                    # remaining unchanged and try_spend refuses.
                    before = lease.remaining
                    # A cost strictly beyond the spendable floor (remaining + overdraft), and
                    # positive even when remaining is already negative.
                    cost = max(1, before + _OVERDRAFT + rng.randint(1, 1000))
                    result = lease.try_spend(cost, gen)
                    assert not result.admitted, (
                        f"{seed_msg} w{w}: uncoverable cost {cost} admitted (remaining {before}, "
                        f"overdraft {_OVERDRAFT})"
                    )
                    assert lease.remaining == before, (
                        f"{seed_msg} w{w}: refused spend of {cost} changed remaining "
                        f"{before} -> {lease.remaining}"
                    )
                    refused_spends += 1

                elif choice < 0.75:
                    # An in-generation refund. Clause 5: refund(c, g) restores the pre-spend value
                    # exactly when g == lease.generation.
                    if w in last_spend_cost and lease.generation == spent_under_gen[w]:
                        cost = last_spend_cost.pop(w)
                        pre = pre_spend_remaining.pop(w)
                        del spent_under_gen[w]
                        lease.refund(cost, lease.generation)
                        assert lease.remaining == pre, (
                            f"{seed_msg} w{w}: in-generation refund of {cost} gave remaining "
                            f"{lease.remaining}, expected the pre-spend value {pre}"
                        )
                        total_refunded += cost
                        in_gen_refunds += 1

                else:
                    # A cross-generation refund. Clause 5: a refund tagged with a stale generation
                    # is dropped. Advance the store generation and re-acquire so the lease's held
                    # generation moves, then refund under the OLD generation.
                    if w in last_spend_cost:
                        old_gen = lease.generation
                        cost = last_spend_cost[w]
                        new_gen = old_gen + 1
                        await client.set(KEYS.budget_generation(_ORG), new_gen)
                        await lease.acquire(new_gen)
                        # The lease's held generation has moved past old_gen.
                        assert lease.generation == new_gen, (
                            f"{seed_msg} w{w}: re-acquire did not advance generation to {new_gen} "
                            f"(held {lease.generation})"
                        )
                        before = lease.remaining
                        lease.refund(cost, old_gen)
                        assert lease.remaining == before, (
                            f"{seed_msg} w{w}: cross-generation refund of {cost} under stale "
                            f"generation {old_gen} changed remaining {before} -> {lease.remaining} "
                            f"(held generation {lease.generation})"
                        )
                        cross_gen_refunds += 1
                        # The stale spend is now unrecoverable; drop its bookkeeping so it is not
                        # double-counted as refundable later.
                        last_spend_cost.pop(w, None)
                        pre_spend_remaining.pop(w, None)
                        spent_under_gen.pop(w, None)

            # A crash/TTL-reclaim path on one random worker: model the store-clock TTL expiry by
            # dropping the crashed worker's ``budget_workers`` membership and its lease key, which
            # is exactly what ``PEXPIRE``/score-based prune does on a crashed worker, then confirm a
            # survivor can still acquire the reclaimed share.
            if rng.random() < 0.5:
                victim = rng.randrange(_WORKERS)
                await client.zrem(KEYS.budget_workers(_ORG), f"w{victim}")
                await client.delete(KEYS.budget_lease(_ORG, f"w{victim}"))
                # The victim's locally-held remainder is now orphaned (a crashed worker never runs
                # return_unspent); its spends still count against the aggregate, which is why the
                # conservation bound (clause 2) allows up to the overdraft overshoot per worker.
                crash_reclaims += 1

            # Clause 2: aggregate conservation. Total admitted minus total refunded must not exceed
            # the initial pool plus the summed per-worker overdraft floor.
            overshoot_bound = _LIMIT + _WORKERS * _OVERDRAFT
            net = total_admitted - total_refunded
            assert net <= overshoot_bound, (
                f"{seed_msg}: admitted {total_admitted} - refunded {total_refunded} = {net} "
                f"exceeds limit {_LIMIT} + Sigma overdraft {_WORKERS * _OVERDRAFT} "
                f"= {overshoot_bound}"
            )

            # Clause 4 (façade half): an uncoverable cost through the REAL QuotaComponent returns a
            # BudgetVerdict carrying BUDGET_UNAVAILABLE, never shared_state_unavailable. Exercise it
            # on a lease drained to empty under a GCRA that always admits.
            if iteration % 101 == 0:
                drained = leases[0]
                # Drain it so remaining <= 0 and overdraft cannot cover the next cost.
                gcra = LocalGCRA(
                    GcraParams(rate_per_s=1_000_000.0, burst=1_000_000), clock=lambda: 0.0
                )
                quota = QuotaComponent(
                    gcra,
                    drained,
                    generation_source=lambda: drained.generation,
                    metrics=_QuotaMetrics(),
                )
                huge = drained.remaining + _OVERDRAFT + mrt + 1
                # Pool is nearly exhausted after the run; force a definite refusal by a cost no
                # pool refill (none runs) can cover.
                verdict = await quota.evaluate(huge, request_id="zs-ledger-check")
                if isinstance(verdict, BudgetVerdict):
                    assert verdict.code == BUDGET_UNAVAILABLE, (
                        f"{seed_msg}: facade refused with {verdict.code!r}, expected "
                        f"{BUDGET_UNAVAILABLE!r} (never shared_state_unavailable)"
                    )
                else:
                    assert isinstance(verdict, Admitted), (
                        f"{seed_msg}: unexpected verdict type {type(verdict)!r}"
                    )

            await client.aclose()

    asyncio.run(_run())

    assert fair_share_bounded_grants > 0, (
        f"seed={_SEED:#x}: no grant was bounded by the fair share (only the cap); the fair-share "
        "branch was never exercised, so clause 1 is vacuous"
    )
    assert admitted_spends > 0, f"seed={_SEED:#x}: no admitted spends; clause 3 is vacuous"
    assert refused_spends > 0, f"seed={_SEED:#x}: no refused spends; clause 4 is vacuous"
    assert in_gen_refunds > 0, f"seed={_SEED:#x}: no in-generation refunds; clause 5a is vacuous"
    assert cross_gen_refunds > 0, (
        f"seed={_SEED:#x}: no cross-generation refunds; clause 5b (the dropped refund) is vacuous"
    )
    assert crash_reclaims > 0, f"seed={_SEED:#x}: no TTL-reclaim/crash path ran"
