"""Property test for per-org budget isolation and owner attribution (GW36 S4, task 4.3).

Feature: gw36-s4-admission-quota, Property 1: Per-org budget isolation and owner attribution
Validates: Requirements 4.4, 4.6, 4.7, 6.1, 6.2, 6.3, 6.4

PROPERTY (design.md Correctness Properties §P1). For any interleaved sequence of
``evaluate`` / ``refund`` calls across two or more orgs, every spend and every refund changes
ONLY the lease of the owner named on that call, leaving every other org's
``{rv2}:budget:{org}:remaining`` balance byte-identical; an ``Admitted`` outcome reports
``owner_id`` equal to the owner passed to ``evaluate``; and a cost the owner's lease cannot cover
refuses with ``budget_unavailable`` (NEVER ``shared_state_unavailable``) having spent zero tokens.

How this exercises the real isolation. The test builds the REAL composition the production boot
wires — the :class:`~gateway_v2.edge.quota_gate.QuotaGate` over a per-key
:class:`~gateway_v2.admit.gcra_cache.GcraCache` and a per-org
:class:`~gateway_v2.admit.quota_registry.QuotaRegistry` whose injected ``make_component`` factory
builds a REAL :class:`~gateway_v2.admit.quota.QuotaComponent` over a REAL
:class:`~gateway_v2.admit.lease.BudgetLease` on a shared ``fakeredis`` store. So a spend actually
decrements that org's ``{rv2}:budget:{org}:remaining`` pool key and we assert cross-org balances
read straight from the store never move. The GCRA is given a very high per-key rate so it always
admits, isolating the budget behaviour under test (the GCRA allowance is Property 3's job).

House idiom (copied from ``tests/admit/test_lgw06_lease_conservation.py`` and the sibling
``test_gw36_s4_gcra_cache_property.py``): a seeded ``random.Random`` loop of ``>= 10,000``
iterations with the seed logged in every assertion message; NO ``hypothesis``. ``evaluate`` is
async, so each iteration's interleaved op sequence runs inside one ``asyncio.run`` over a fresh
store; ``refund`` is synchronous. The injected off-path ``spawn`` closes the refill coroutine
without running it so the ledger stays deterministic (the known-benign "BudgetLease._refill never
awaited" warning, documented in AGENTS.md, is NOT a failure). Test files are not under the
import-linter layer contract.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

import fakeredis

from gateway_v2.admit.budget_state import BudgetRecord, BudgetSnapshot
from gateway_v2.admit.gcra_cache import GcraCache
from gateway_v2.admit.grant import Admitted, BudgetVerdict
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.quota import QuotaComponent
from gateway_v2.admit.quota_registry import QuotaRegistry
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE, SHARED_STATE_UNAVAILABLE
from gateway_v2.edge.quota_gate import QuotaGate
from gateway_v2.runtime.store_keys import KEYS

# The house standard is >= 10,000 iterations (AGENTS.md), well above the task's >= 100 floor.
_ITERATIONS = 10_000

# A lease chunk far larger than any single cost here, so one acquired chunk covers a whole op
# sequence: the only way a spend is refused is the org pool itself being unable to cover it.
_CHUNK = 1_000_000
# A per-key GCRA rate so high it always admits — isolating the per-org BUDGET behaviour from the
# rate check (the GCRA allowance is Property 3). burst is large for the same reason.
_FAST_RATE = 1_000_000.0
_FAST_BURST = 1_000_000


def _drop(work: Awaitable[None]) -> None:
    """Off-path refill ``spawn`` that never runs the refill (keeps the ledger deterministic).

    Closes the coroutine so no store I/O happens under it; matches the idiom in
    ``test_v2_leaks_budget.py``. The "coroutine was never awaited" warning is known-benign.
    """
    close = getattr(work, "close", None)
    if callable(close):
        close()


class _QuotaMetrics:
    """A tiny counting metrics double satisfying the lease / component metrics surface."""

    __slots__ = ("budget_unavailable", "refills")

    def __init__(self) -> None:
        self.refills = 0
        self.budget_unavailable = 0

    def observe_async_refill(self) -> None:
        self.refills += 1

    def observe_budget_unavailable(self) -> None:
        self.budget_unavailable += 1


def _principal(org: str) -> Principal:
    """A resolved principal with a rate so high the per-key GCRA always admits."""
    return Principal(
        key_id=f"key-{org}",
        org_id=org,
        rate_per_s=_FAST_RATE,
        burst=_FAST_BURST,
        feed_seq=0,
    )


def _make_component_factory(
    client: Any,
) -> tuple[object, dict[str, BudgetLease]]:
    """Build the registry's injected ``make_component`` over ``client`` and expose each lease.

    Returns ``(factory, leases)`` where ``factory(org, record)`` builds a REAL ``QuotaComponent``
    over a REAL ``BudgetLease`` on the shared ``client`` and records the lease in ``leases`` keyed
    by org (so the test can read ``lease.remaining`` as a second, in-memory, view of isolation).
    The factory is what the production boot (task 10.3) injects, closing over the store client, the
    contract-derived chunk/watermark, the worker id, and ``spawn``.
    """
    leases: dict[str, BudgetLease] = {}

    def factory(org: str, record: BudgetRecord) -> QuotaComponent:
        lease = BudgetLease(
            client,
            LeaseConfig(org=org, chunk=_CHUNK, low_watermark=0, ttl_s=30.0),
            worker_id="w0",
            clock=lambda: 0.0,
            spawn=_drop,
            metrics=_QuotaMetrics(),
        )
        leases[org] = lease
        gcra = _AlwaysAdmit()
        return QuotaComponent(
            gcra,  # type: ignore[arg-type]
            lease,
            generation_source=(lambda gen=record.generation: gen),
            metrics=_QuotaMetrics(),
        )

    return factory, leases


class _AlwaysAdmit:
    """A GCRA double the component-internal check always admits (its own rate is not under test).

    The composed ``QuotaGate`` already runs the per-key ``GcraCache`` GCRA before the component;
    the component carries a second internal GCRA. Both are given an always-admit allowance so the
    only decision left is the per-org budget spend (Property 1's subject). Satisfies the
    ``LocalGCRA.admit`` surface structurally.
    """

    __slots__ = ()

    def admit(self, now: float | None = None) -> bool:
        del now
        return True


@dataclass(slots=True)
class _OrgSetup:
    """One org's pool, its served record, and the in-memory lease view for isolation checks."""

    org: str
    pool: int


async def _store_remaining(client: Any, org: str) -> int:
    """Read the LIVE pool counter ``{rv2}:budget:{org}:remaining`` straight from the store."""
    raw = await client.get(KEYS.budget_remaining(org))
    return int(raw) if raw is not None else 0


async def _available(client: Any, org: str, leases: dict[str, BudgetLease]) -> int:
    """The org's TOTAL observable remaining budget: store pool + its worker's held lease.

    The design spends LOCALLY — a chunk is acquired from ``{rv2}:budget:{org}:remaining`` into the
    worker's in-memory ``BudgetLease.remaining`` and then drawn down there, refilling from the pool
    off the request path. So the balance that must not move for a non-owner is the SUM of what is
    still in that org's store pool and what its worker still holds in its lease. Reading the store
    key directly (per task 4.3) is one half of that sum; the held lease is the other. Either half
    moving for a non-owner would be a cross-org leak.
    """
    held = leases[org].remaining if org in leases else 0
    return await _store_remaining(client, org) + held


async def _run_sequence(
    seed: int,
    i: int,
    orgs: list[_OrgSetup],
    ops: list[tuple[str, str, int]],
) -> None:
    """Build the real gate over a fresh store, run the op sequence, assert P1 after every call."""
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    records: dict[str, BudgetRecord] = {}
    for setup in orgs:
        await client.set(KEYS.budget_remaining(setup.org), setup.pool)
        await client.set(KEYS.budget_generation(setup.org), 0)
        records[setup.org] = BudgetRecord(org=setup.org, pool_tokens=setup.pool, generation=0)

    snapshot = BudgetSnapshot()
    for record in records.values():
        snapshot.replace(record)
    factory, leases = _make_component_factory(client)
    registry = QuotaRegistry(snapshot, make_component=factory)  # type: ignore[arg-type]
    gcra_cache = GcraCache(worker_count=1, clock=lambda: 0.0)
    gate = QuotaGate(gcra_cache, registry)

    all_orgs = [setup.org for setup in orgs]

    # Pre-warm each org's component (sync build via the registry) and acquire its first lease chunk
    # under generation 0 off the request path — exactly as the harness's ``prime`` does. Without a
    # held chunk the first ``try_spend`` refuses and only SCHEDULES a refill (which ``spawn=_drop``
    # discards), so a spend would never actually decrement the store pool. Priming lets real spends
    # draw the real ``{rv2}:budget:{org}:remaining`` counter down, which is what isolation is read
    # against. A zero pool grants nothing, so that org stays correctly refusing.
    for org in all_orgs:
        registry.component_for(org)
    for lease in leases.values():
        await lease.acquire(0)

    for op_index, (op, owner, cost) in enumerate(ops):
        # Snapshot every org's TOTAL observable remaining (store pool + its held lease) BEFORE the
        # call. Isolation is asserted against this sum, which reads the ``{rv2}:budget:{org}:
        # remaining`` counter directly (per task 4.3) plus the in-memory lease half it was leased
        # into.
        before = {org: await _available(client, org, leases) for org in all_orgs}
        before_store = {org: await _store_remaining(client, org) for org in all_orgs}
        ctx = (
            f"seed={seed:#x} iter={i} op#{op_index}={op} owner={owner} cost={cost} before={before}"
        )

        if op == "evaluate":
            result = await gate.evaluate(
                cost,
                request_id=f"req-{i}-{op_index}",
                owner=owner,
                principal=_principal(owner),
                key_hash=f"hash-{owner}",
            )
            after = {org: await _available(client, org, leases) for org in all_orgs}
            after_store = {org: await _store_remaining(client, org) for org in all_orgs}

            # (Req 4.4, 4.6, 6.1) Every org OTHER than the owner is byte-identical — both its total
            # available AND its raw store pool counter: a spend for one org touches no other org's
            # lease or pool, and one org's state never gates another's.
            for org in all_orgs:
                if org == owner:
                    continue
                assert after[org] == before[org], (
                    f"{ctx}: a spend for {owner!r} changed {org!r}'s available "
                    f"{before[org]} -> {after[org]} (after={after})"
                )
                assert after_store[org] == before_store[org], (
                    f"{ctx}: a spend for {owner!r} changed {org!r}'s store remaining "
                    f"{before_store[org]} -> {after_store[org]}"
                )

            if isinstance(result, Admitted):
                # (Req 6.4) The admitted owner_id equals the owner passed to evaluate.
                assert result.owner_id == owner, (
                    f"{ctx}: Admitted.owner_id {result.owner_id!r} != owner {owner!r}"
                )
                # An admitted spend of ``cost`` draws exactly ``cost`` from the owner's total
                # available (and nothing from anyone else, asserted above).
                assert after[owner] == before[owner] - cost, (
                    f"{ctx}: admitted spend drew {before[owner] - after[owner]} from {owner!r}, "
                    f"expected {cost}"
                )
            else:
                assert isinstance(result, BudgetVerdict), (
                    f"{ctx}: a non-Admitted result must be a BudgetVerdict, got {result!r}"
                )
                # (Req 4.7) A refusal is the NARROW budget_unavailable, never
                # shared_state_unavailable.
                assert result.code == BUDGET_UNAVAILABLE, (
                    f"{ctx}: refusal code {result.code!r} must be {BUDGET_UNAVAILABLE!r}"
                )
                assert result.code != SHARED_STATE_UNAVAILABLE, (
                    f"{ctx}: refusal must never be {SHARED_STATE_UNAVAILABLE!r}"
                )
                # (Req 4.7) A refused request spends zero tokens — the owner's available and raw
                # store pool are both unchanged.
                assert after[owner] == before[owner], (
                    f"{ctx}: a refused evaluate spent tokens: owner available "
                    f"{before[owner]} -> {after[owner]}"
                )
                assert after_store[owner] == before_store[owner], (
                    f"{ctx}: a refused evaluate moved the owner's store pool "
                    f"{before_store[owner]} -> {after_store[owner]}"
                )
        else:  # refund
            # refund is synchronous and credits ONLY the owner's in-memory lease (no store I/O).
            gate.refund(cost, request_id=f"req-{i}-{op_index}", owner=owner)
            after = {org: await _available(client, org, leases) for org in all_orgs}
            after_store = {org: await _store_remaining(client, org) for org in all_orgs}
            # (Req 6.2, 6.3) A refund credits ONLY the owner's lease — every OTHER org's available
            # and store pool are unchanged; and no org's raw store pool moves at all (refund is
            # local). The owner's available rises by exactly ``cost`` when ``cost > 0``.
            for org in all_orgs:
                if org != owner:
                    assert after[org] == before[org], (
                        f"{ctx}: a refund for {owner!r} changed {org!r}'s available "
                        f"{before[org]} -> {after[org]}"
                    )
                assert after_store[org] == before_store[org], (
                    f"{ctx}: a refund for {owner!r} moved {org!r}'s store pool "
                    f"{before_store[org]} -> {after_store[org]} (refund is local, no store write)"
                )
            expected_owner = before[owner] + (cost if cost > 0 else 0)
            assert after[owner] == expected_owner, (
                f"{ctx}: a refund of {cost} credited the owner's available "
                f"{before[owner]} -> {after[owner]}, expected {expected_owner}"
            )

    await client.aclose()


# --------------------------------------------------------------------------- #
# Property 1: Per-org budget isolation and owner attribution
# Feature: gw36-s4-admission-quota, Property 1: Per-org budget isolation and owner attribution
# Validates: Requirements 4.4, 4.6, 4.7, 6.1, 6.2, 6.3, 6.4
# --------------------------------------------------------------------------- #


def test_property1_per_org_budget_isolation_and_owner_attribution() -> None:
    """Interleaved evaluate/refund across 2-5 orgs: isolation + owner attribution + fail-closed.

    For each seeded iteration: pick 2-5 orgs each with its own pool (spanning tiny to large) and a
    random sequence of ``(op, owner, cost)`` with ``op in {evaluate, refund}``, ``owner`` drawn
    from the org set, and ``cost`` spanning below / at / above the pools. Drive them through the
    REAL ``QuotaGate`` -> ``QuotaRegistry`` -> per-org ``QuotaComponent`` / ``BudgetLease`` over a
    shared ``fakeredis`` store, asserting after EVERY call that only the named owner's live pool
    moved, that an ``Admitted`` reports the passed owner, and that an over-budget cost refuses
    ``budget_unavailable`` (never ``shared_state_unavailable``) with zero tokens spent.
    """
    seed = 0x5_4_01
    rng = random.Random(seed)

    async def _iteration(i: int) -> None:
        n_orgs = rng.randint(2, 5)
        orgs = [
            _OrgSetup(
                org=f"org-{i}-{n}",
                # Pools spanning tiny (so costs can exceed them) to large (so spends succeed).
                pool=rng.choice((0, 1, rng.randint(2, 50), rng.randint(1_000, 1_000_000))),
            )
            for n in range(n_orgs)
        ]
        max_pool = max((setup.pool for setup in orgs), default=0)
        op_count = rng.randint(2, 12)
        ops: list[tuple[str, str, int]] = []
        for _ in range(op_count):
            op = rng.choice(("evaluate", "evaluate", "refund"))  # bias toward spends
            owner = rng.choice(orgs).org
            # cost spans below / at / above the largest pool, and also small values.
            cost = rng.choice(
                (
                    0,
                    1,
                    rng.randint(1, max(1, max_pool)),
                    max_pool,
                    max_pool + rng.randint(1, 1_000),
                )
            )
            ops.append((op, owner, cost))
        await _run_sequence(seed, i, orgs, ops)

    async def _driver() -> None:
        for i in range(_ITERATIONS):
            await _iteration(i)

    asyncio.run(_driver())
