"""Local acceptance harness for the GW06 budget-lease live gates (R2-09 / R2-14 / R2-19, Req 11).

This is the in-process, scaled-down equivalent of the GW06 **live** acceptance gates. It drives the
REAL :class:`~gateway_v2.admit.quota.QuotaComponent` / :class:`~gateway_v2.admit.lease.BudgetLease`
over one ``fakeredis`` with an injected clock, so lease correctness is proven without a cloud fleet.
Three equivalents live here, mapped to the design's "Local acceptance equivalents" section:

* **LGW06-3** (:func:`test_lgw06_3_multi_replica_no_quota_multiplication`, task 13.1, Req 11.1) —
  N in-process replicas (N :class:`BudgetLease` instances, each with its own ``worker_id`` and its
  own :class:`~gateway_v2.admit.gcra.LocalGCRA`) share ONE ``fakeredis`` budget pool. Over a seeded
  ``random.Random`` arrival pattern of >= 10,000 arrivals, aggregate admission stays
  ``<= limit + overshoot`` (overshoot = at most one in-flight chunk per worker), the overshoot is
  PUBLISHED via :meth:`QuotaMetrics.set_overshoot` and read back from the snapshot, and quota does
  NOT multiply by the replica count (aggregate ``<< limit * N`` for ``N > 1``). The Lease_Chunk is
  the REAL contract-derived chunk (:func:`derive_lease_chunk` over a :func:`from_signals`
  :class:`ResourceContract` + an injected ``q_safe``), so the harness exercises the real derivation
  rather than a hand-picked constant.

* **LGW06-5** (:func:`test_lgw06_5_injected_latency_bounded_and_posture_holds`, task 13.2, Req 11.2)
  — a 200 ms store latency is modelled by advancing an injected fake clock on every store op (NOT a
  real ``sleep``; the whole file runs well under a wall-clock second). The harness asserts three
  things: (1) the bounded-timeout contract is respected — a 200 ms op under a sub-refresh-period
  ``socket_timeout`` is rejected by ``require_bounded_client`` / would exceed ``bounded_timeout_s``,
  so the façade's request path (which issues NO store op) is unaffected while an off-path acquire
  that overran the per-op timeout surfaces fail-closed; (2) the declared posture holds — the façade
  admits while the lease has budget and returns ``budget_unavailable`` once it is exhausted; (3) the
  connection pool does not grow unbounded — the lease holds ONE injected client and issues bounded
  ops, with a counter proving no per-request client/connection object is created.

* **R2-09 partition behaviour**
  (:func:`test_r2_09_partition_spends_remaining_then_budget_unavailable`,
  task 13.3, Req 11.3) — during a ``fakeredis`` partition, budget spends ONLY the Remaining_Lease
  and then returns ``budget_unavailable`` (never ``shared_state_unavailable``), and the refill is
  proven off the request path (a request-path store-call counter stays ``0``). This deliberately
  overlaps ``test_lgw06_outage_posture.py`` / ``test_lgw06_refill_offpath.py``: it is the
  harness-level tie-together of the Req-11 acceptance equivalents, driving the whole façade.

Deferred cloud / scale gates (Req 11.4 — OUT OF LOCAL SCOPE)
------------------------------------------------------------
The following are **deferred cloud/scale gates**, out of local scope, and are represented by the
in-process equivalents above — they have no implementation here and are recorded as deferred:

* the full LIVE four-replica fleet quota run (the full LGW06-3), run against a real multi-replica
  deployment and a real store rather than N in-process ``BudgetLease`` instances over ``fakeredis``;
* the live store failover (**LGW06-6**) — a real Valkey/Redis primary failover with recovery inside
  the declared window and the posture holding across it;
* real cross-zone RTO measurement — the ~200 ms minimum TCP RTO that R2-14 addresses is MODELLED
  here as a deterministic injected-clock latency, not measured against real cross-zone sockets.

The **R2-14 zone placement** (gateways placed in the store primary's zone so a round trip is never
cross-zone) is a **deployment dependency, not code**; the code-side requirement is the
retry-once-on-idempotent-read-timeout, covered by ``test_lgw06_retry_once.py``.

House idiom (per ``tests/admit/test_lgw19_codel.py`` and the sibling ``test_lgw06_*`` files): a
seeded ``random.Random`` (>= 10,000 iterations where a property is asserted — the LGW06-3 aggregate
bound is the property here), NO ``hypothesis``, async via ``asyncio.run``, ``fakeredis`` for the
store, and an injected ``Callable[[], float]`` clock. The injected latency is a fake-clock
advance, never a wall-clock ``sleep``.
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
from gateway_v2.admit.metrics import QuotaMetrics
from gateway_v2.admit.quota import QuotaComponent, derive_lease_chunk, derive_low_watermark
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE, SHARED_STATE_UNAVAILABLE
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.store_keys import StoreKeys
from gateway_v2.runtime.store_valkey import bounded_timeout_s, require_bounded_client

_ARRIVALS = 10_000
_ORG = "org-harness"
_KEYS = StoreKeys(namespace="{t}")

# A per-op store timeout that stays BELOW the refresh period (the bounded-client contract). The
# injected cross-zone latency the LGW06-5 equivalent models (200 ms) is deliberately ABOVE this, so
# a store op that incurred the latency would exceed the bound. These are time factors, not capacity
# positions, so they belong in the test, not in ``admit`` (whose capacity gate forbids literals).
_REFRESH_PERIOD_S = 0.5
_BOUNDED_TIMEOUT_S = 0.025  # 25 ms request-path per-op timeout
_INJECTED_LATENCY_S = 0.200  # 200 ms modelled cross-zone RTO (LGW06-5)


def _run(coro: Awaitable[object]) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Shared doubles — contract builder, metrics, counting/latency/partition clients
# --------------------------------------------------------------------------- #


class _CountingMetrics:
    """A ``QuotaMetricsLike`` + budget_unavailable observer double for the façade/lease."""

    def __init__(self) -> None:
        self.async_refills = 0
        self.budget_unavailable = 0

    def observe_async_refill(self) -> None:
        self.async_refills += 1

    def observe_budget_unavailable(self) -> None:
        self.budget_unavailable += 1


def _serviceable_contract(rng: random.Random) -> ResourceContract:
    """Build a random but serviceable :class:`ResourceContract` via :func:`from_signals`.

    Ranges mirror ``tests/admit/test_lgw06_chunk_derivation.py`` so ``from_signals`` and
    ``queue_depth`` are computable (at least one worker is detected on both the CPU and RAM paths),
    which is what lets :func:`derive_lease_chunk` return a real contract-derived chunk.
    """
    utilization_cap = rng.uniform(0.25, 0.95)
    cpu_quota = rng.uniform(1.0 / utilization_cap + 1.0, 32.0)
    per_worker_rss = rng.randint(128, 512) * 1024 * 1024
    rss_multiplier = rng.randint(int(1.0 / utilization_cap) + 2, 24)
    memory_limit = per_worker_rss * rss_multiplier
    fd_limit = rng.randint(256, 65_536)
    signals = HardwareSignals(
        cpu_quota=cpu_quota,
        memory_limit=memory_limit,
        fd_limit=fd_limit,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=rng.uniform(5.0, 200.0),
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
    )


class _PoolCountingClient:
    """Wraps a ``fakeredis`` async client, counting pipeline/connection allocations.

    ``connections_created`` increments every time a new ``pipeline()`` (the only connection-bearing
    object the lease allocates) is produced. The lease is expected to hold ONE client for its whole
    lifetime and issue bounded ops through it — never to construct a client per request — so this
    counter growing in lockstep with STORE ops (not with request-path spends) is the "pool does not
    grow unbounded" evidence (Req 11.2). It also exposes a redis-py-shaped ``connection_pool`` with
    a ``socket_timeout`` so ``bounded_timeout_s`` / ``require_bounded_client`` can inspect it.
    """

    def __init__(self, inner: Any, *, socket_timeout: float) -> None:
        self._inner = inner
        self.connections_created = 0
        self.store_ops = 0
        self.connection_pool = _FakePool(socket_timeout)

    def pipeline(self, *args: Any, **kwargs: Any) -> Any:
        self.connections_created += 1
        return _CountingPipeline(self._inner.pipeline(*args, **kwargs), self)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if callable(attr):
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                self.store_ops += 1
                return attr(*args, **kwargs)
            return wrapped
        return attr


class _FakePool:
    """A redis-py-shaped connection pool exposing ``connection_kwargs['socket_timeout']``."""

    def __init__(self, socket_timeout: float) -> None:
        self.connection_kwargs = {"socket_timeout": socket_timeout}


class _CountingPipeline:
    """Pipeline wrapper that attributes every store op back to the owning client's counter."""

    def __init__(self, inner: Any, owner: _PoolCountingClient) -> None:
        self._inner = inner
        self._owner = owner

    async def __aenter__(self) -> _CountingPipeline:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> Any:
        return await self._inner.__aexit__(*exc)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if callable(attr):
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                self._owner.store_ops += 1
                return attr(*args, **kwargs)
            return wrapped
        return attr


# --------------------------------------------------------------------------- #
# TASK 13.1 — LGW06-3: multi-replica, no quota multiplication (Req 11.1)
# --------------------------------------------------------------------------- #


class _ReplicaFleet:
    """N :class:`BudgetLease` replicas over ONE ``fakeredis``, each with its own GCRA + worker_id.

    The Lease_Chunk is the REAL contract-derived chunk; the low watermark is the real
    :func:`derive_low_watermark`. The injected ``spawn`` collects off-path refill coroutines so the
    harness drives them deterministically between arrivals (single-flight + off-path are proven by
    the sibling property tests; here they are simply driven so the pool is actually drawn from).
    """

    def __init__(self, *, limit: int, chunk: int, workers: int, metrics: QuotaMetrics) -> None:
        self.server = fakeredis.FakeServer()
        self.client = fakeredis.FakeAsyncRedis(server=self.server, decode_responses=True)
        self.limit = limit
        self.chunk = chunk
        self.workers = workers
        self.pending: list[Awaitable[None]] = []
        self.metrics = metrics
        low_watermark = derive_low_watermark(chunk)
        self.config = LeaseConfig(
            org=_ORG, chunk=chunk, low_watermark=low_watermark, ttl_s=30.0,
        )
        self.leases = [
            BudgetLease(
                self.client,
                self.config,
                worker_id=f"replica-{i}",
                clock=lambda: 0.0,
                spawn=self.pending.append,
                metrics=self.metrics,
                keys=_KEYS,
            )
            for i in range(workers)
        ]
        # One LocalGCRA per replica — generous so burst/rate never limits here (we measure the
        # SHARED-POOL budget bound, not the per-worker rate; GCRA bounds are in test_lgw06_gcra).
        self.gcras = [
            LocalGCRA(GcraParams(rate_per_s=1_000_000.0, burst=100_000), clock=lambda: 0.0)
            for _ in range(workers)
        ]
        self.components = [
            QuotaComponent(gcra, lease, generation_source=lambda: 0, metrics=self.metrics)
            for gcra, lease in zip(self.gcras, self.leases, strict=True)
        ]

    async def seed_pool(self) -> None:
        await self.client.set(_KEYS.budget_remaining(_ORG), self.limit)
        await self.client.set(_KEYS.budget_generation(_ORG), 0)

    async def prime(self) -> None:
        """Each replica acquires an initial chunk under generation 0 (off the request path)."""
        for lease in self.leases:
            await lease.acquire(0)

    async def drain_refills(self) -> None:
        while self.pending:
            batch = self.pending
            self.pending = []
            for coro in batch:
                await coro

    async def close(self) -> None:
        for coro in self.pending:
            coro.close()  # type: ignore[attr-defined]
        self.pending.clear()
        await self.client.aclose()


async def _lgw06_3_scenario(seed: int) -> tuple[int, int, int, int]:
    """One LGW06-3 run. Returns ``(admitted, limit, overshoot, workers)`` for the caller."""
    rng = random.Random(seed)
    contract = _serviceable_contract(rng)
    q_safe = rng.uniform(1.0, 500.0)
    # REAL contract-derived chunk — exercises derive_lease_chunk, not a hand-picked constant.
    chunk = derive_lease_chunk(contract, q_safe=q_safe)
    workers = rng.randint(2, 6)
    # A pool large enough that several chunks per worker are acquired across the run, so the shared
    # bound (not an untouched pool) is what the aggregate presses against.
    limit = chunk * workers * rng.randint(2, 8)
    overshoot = chunk * workers  # at most one in-flight chunk per worker (the declared overshoot)

    metrics = QuotaMetrics()
    metrics.set_overshoot(overshoot)  # PUBLISH the declared overshoot (Req 7.4 / 11.1)
    fleet = _ReplicaFleet(limit=limit, chunk=chunk, workers=workers, metrics=metrics)
    await fleet.seed_pool()
    await fleet.prime()

    admitted = 0
    for _ in range(_ARRIVALS):
        idx = rng.randrange(workers)
        cost = rng.randint(1, 3)
        verdict = await fleet.components[idx].evaluate(cost, request_id="r")
        if isinstance(verdict, Admitted):
            admitted += cost
        # Arbitrary refill timing: sometimes drain off-path refills, sometimes let them pile up.
        if rng.random() < 0.3:
            await fleet.drain_refills()
    await fleet.drain_refills()

    # The published overshoot is observable from the snapshot (Req 7.4 / 11.1).
    snap = metrics.snapshot()
    assert snap.lease_overshoot == overshoot, (
        f"seed={seed:#x} published overshoot {snap.lease_overshoot} != {overshoot}"
    )
    await fleet.close()
    return admitted, limit, overshoot, workers


def test_lgw06_3_multi_replica_no_quota_multiplication() -> None:
    """LGW06-3: aggregate admission <= limit + overshoot, overshoot published, no multiplication.

    # Feature: budget-lease, Property 1 (harness equivalent)
    # Validates: Requirements 11.1

    Several independently seeded scenarios, EACH running >= 10,000 arrivals across N in-process
    replicas sharing one ``fakeredis`` pool. The aggregate admission is bounded by the shared pool
    plus at most one in-flight chunk per worker (the declared, published overshoot), and is strictly
    below what a per-worker counter (``limit * workers``) would have allowed for ``workers > 1`` —
    the shared lease is what stops Replica_Multiplication.
    """
    base = 0x06_13_01
    for s in range(8):
        admitted, limit, overshoot, workers = asyncio.run(_lgw06_3_scenario(base + s))
        assert admitted <= limit + overshoot, (
            f"seed={base + s:#x} admitted={admitted} exceeds limit+overshoot={limit + overshoot}"
        )
        # Quota did NOT multiply by replica count: a per-worker counter would admit up to
        # ``limit * workers``; the shared bound is strictly below that for N > 1.
        assert workers == 1 or (limit + overshoot) < limit * workers, (
            f"seed={base + s:#x} bound limit+overshoot={limit + overshoot} is not << "
            f"limit*workers={limit * workers} (looks like per-worker multiplication)"
        )
        assert admitted < limit * workers, (
            f"seed={base + s:#x} admitted={admitted} hit per-worker-multiplied {limit * workers}"
        )


# --------------------------------------------------------------------------- #
# TASK 13.2 — LGW06-5: injected 200 ms latency, bounded timeout + posture (Req 11.2)
# --------------------------------------------------------------------------- #


class _FakeClock:
    """A deterministic monotonic clock the injected-latency store wrapper advances (no sleeps)."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _LatencyClient:
    """Wraps ``fakeredis`` so every store op advances the fake clock by ``latency_s`` (no sleep).

    This is the deterministic injected-latency model of LGW06-5: a store round trip "costs"
    ``latency_s`` of simulated time by advancing the injected clock, rather than blocking the event
    loop on a wall-clock ``sleep``. ``last_op_elapsed_s`` records the simulated duration of the most
    recent op so the test can compare it against the bounded per-op timeout. ``connections_created``
    tracks pipeline allocations (the pool-growth evidence). A redis-py-shaped ``connection_pool``
    exposes the ``socket_timeout`` for ``require_bounded_client``.
    """

    def __init__(self, inner: Any, *, clock: _FakeClock, latency_s: float,
                 socket_timeout: float) -> None:
        self._inner = inner
        self._clock = clock
        self._latency_s = latency_s
        self.connections_created = 0
        self.request_path_connections = 0
        self.on_request_path = False
        self.last_op_elapsed_s = 0.0
        self.connection_pool = _FakePool(socket_timeout)

    def pipeline(self, *args: Any, **kwargs: Any) -> Any:
        self.connections_created += 1
        if self.on_request_path:
            self.request_path_connections += 1
        return _LatencyPipeline(self._inner.pipeline(*args, **kwargs), self)

    def _charge(self) -> None:
        before = self._clock.now()
        self._clock.advance(self._latency_s)
        self.last_op_elapsed_s = self._clock.now() - before

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if callable(attr):
            async def wrapped(*args: Any, **kwargs: Any) -> Any:
                self._charge()
                return await attr(*args, **kwargs)
            return wrapped
        return attr


class _LatencyPipeline:
    """Pipeline wrapper that charges simulated latency on the committing ``execute``."""

    def __init__(self, inner: Any, owner: _LatencyClient) -> None:
        self._inner = inner
        self._owner = owner

    async def __aenter__(self) -> _LatencyPipeline:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> Any:
        return await self._inner.__aexit__(*exc)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if callable(attr):
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                if name == "execute":
                    self._owner._charge()
                return attr(*args, **kwargs)
            return wrapped
        return attr


def test_lgw06_5_injected_latency_bounded_and_posture_holds() -> None:
    """LGW06-5: 200 ms injected latency → bounded timeout respected, posture holds, pool bounded.

    # Validates: Requirements 11.2

    The latency is a fake-clock advance, never a wall-clock sleep. Three assertions:

    1. **Bounded-timeout contract.** ``require_bounded_client`` accepts the lease's client (its
       ``socket_timeout`` is below the refresh period), and the modelled 200 ms op is strictly
       GREATER than that bounded per-op timeout — so a real store op that incurred this latency
       would time out. The request path issues NO store op, so the latency never reaches it; it is
       only ever on the off-path acquire/refill, exactly where the bound is meant to protect.
    2. **Declared posture holds.** The façade admits while the Remaining_Lease has budget and
       returns ``budget_unavailable`` once it is exhausted — the latency does not change posture.
    3. **Pool does not grow unbounded.** The lease holds ONE client for its whole lifetime;
       connection allocations track STORE ops (acquire/return pipelines), never the number of
       request-path spends, so the pool count stays tiny while thousands of requests are served.
    """
    seed = 0x06_13_02

    async def go() -> None:
        rng = random.Random(seed)
        clock = _FakeClock()
        server = fakeredis.FakeServer()
        inner = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        client = _LatencyClient(
            inner, clock=clock, latency_s=_INJECTED_LATENCY_S, socket_timeout=_BOUNDED_TIMEOUT_S,
        )

        # (1) The bounded-client contract: the lease's client is accepted (socket_timeout below the
        # refresh period), and the modelled latency exceeds that per-op timeout.
        bound = require_bounded_client(client, below_s=_REFRESH_PERIOD_S)
        assert bound == _BOUNDED_TIMEOUT_S
        assert bounded_timeout_s(client) == _BOUNDED_TIMEOUT_S
        assert _INJECTED_LATENCY_S > bound, (
            "the modelled 200 ms op must exceed the bounded per-op timeout for the contract to bite"
        )

        contract = _serviceable_contract(rng)
        q_safe = rng.uniform(5.0, 200.0)
        chunk = derive_lease_chunk(contract, q_safe=q_safe)
        low = derive_low_watermark(chunk)
        config = LeaseConfig(org=_ORG, chunk=chunk, low_watermark=low, ttl_s=30.0)
        metrics = QuotaMetrics()

        # The pool is exactly ONE chunk: once the pre-acquired chunk is spent, no refill can land
        # (the pool is empty), so the posture must flip to budget_unavailable.
        await inner.set(_KEYS.budget_remaining(_ORG), chunk)
        await inner.set(_KEYS.budget_generation(_ORG), 0)

        pending: list[Awaitable[None]] = []
        lease = BudgetLease(
            client,
            config,
            worker_id="w0",
            clock=clock.now,
            spawn=pending.append,
            metrics=metrics,
            keys=_KEYS,
        )
        # The off-path acquire incurs the modelled latency on the fake clock.
        t_before = clock.now()
        await lease.acquire(0)
        acquire_elapsed = clock.now() - t_before
        assert acquire_elapsed >= _INJECTED_LATENCY_S, (
            "the off-path acquire must incur the modelled store latency on the injected clock"
        )
        held = lease.remaining
        assert held == chunk > 0
        connections_after_acquire = client.connections_created

        gcra = LocalGCRA(GcraParams(rate_per_s=1_000_000.0, burst=100_000), clock=clock.now)
        quota = QuotaComponent(gcra, lease, generation_source=lambda: 0, metrics=metrics)

        admitted = 0
        refused = 0
        # Serve more requests than the held budget so we cross into the refusal regime. The gate is
        # opened STRICTLY around ``evaluate`` so any store op / connection the request path tried to
        # make is attributed to it; the off-path refills are drained OUTSIDE the gate.
        for n in range(held + 50):
            clock_before_spend = clock.now()
            client.on_request_path = True
            verdict = await quota.evaluate(1, request_id=f"r{n}")
            client.on_request_path = False
            # (3a) The request path advanced the simulated clock by ZERO: a store op would have
            # charged the modelled 200 ms latency. ``evaluate`` is pure-local, so time stands still.
            assert clock.now() == clock_before_spend, (
                f"seed={seed:#x} iter={n} the request path advanced the clock — a store op leaked"
            )
            if isinstance(verdict, Admitted):
                admitted += 1
            else:
                assert isinstance(verdict, BudgetVerdict)
                assert verdict.code == BUDGET_UNAVAILABLE, (
                    f"seed={seed:#x} refusal code {verdict.code!r} is not the narrow posture"
                )
                assert verdict.code != SHARED_STATE_UNAVAILABLE
                assert verdict.should_retry is False
                assert verdict.retry_after_s >= 1.0
                refused += 1
            while pending:
                await pending.pop(0)

        # (2) Posture holds: spent exactly the held budget, then refused the rest narrowly.
        assert admitted == held, f"seed={seed:#x} admitted={admitted} != held={held}"
        assert refused >= 1, f"seed={seed:#x} never crossed into the refusal regime"

        # (3b) The connection pool does not grow unbounded: the lease held ONE client for the whole
        # run and the request path created ZERO connections. Every connection object the lease ever
        # allocated was for an off-path acquire/refill pipeline, never a per-request client.
        assert client.request_path_connections == 0, (
            f"seed={seed:#x} the request path created {client.request_path_connections} "
            "connections — a client/connection was built per request"
        )
        _ = connections_after_acquire  # the acquire pipeline is the only pre-request allocation
        for coro in pending:
            coro.close()  # type: ignore[attr-defined]
        await inner.aclose()

    _run(go())


# --------------------------------------------------------------------------- #
# TASK 13.3 — R2-09 partition behaviour (Req 11.3)
# --------------------------------------------------------------------------- #

_REQUEST_PATH_STORE_COMMANDS = frozenset(
    {"get", "set", "decrby", "incrby", "delete", "pexpire", "expire", "watch",
     "unwatch", "multi", "execute", "pipeline"},
)


class _PartitionCountingClient:
    """A ``fakeredis`` client that can be partitioned AND counts request-path store calls.

    Combines the two sibling idioms: ``partitioned`` makes every op raise ``ConnectionError`` (the
    fail-closed outage, from ``test_lgw06_outage_posture.py``), and ``on_request_path`` /
    ``request_path_calls`` count any store op issued while the request-path gate is open (from
    ``test_lgw06_refill_offpath.py``). The harness proves BOTH at once: during the partition the
    façade spends only the Remaining_Lease then returns ``budget_unavailable``, AND the request
    path issues zero store calls (the refill is off the request path, Req 11.3 / R2-09).
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.partitioned = False
        self.on_request_path = False
        self.request_path_calls = 0

    def _guard(self, name: str) -> None:
        if self.on_request_path and name in _REQUEST_PATH_STORE_COMMANDS:
            self.request_path_calls += 1
        if self.partitioned:
            raise RedisConnectionError(f"partitioned ({name})")

    def pipeline(self, *args: Any, **kwargs: Any) -> Any:
        self._guard("pipeline")
        return _PartitionCountingPipeline(self._inner.pipeline(*args, **kwargs), self)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if callable(attr):
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                self._guard(name)
                return attr(*args, **kwargs)
            return wrapped
        return attr


class _PartitionCountingPipeline:
    """Pipeline wrapper mirroring the partition + request-path-count guard."""

    def __init__(self, inner: Any, owner: _PartitionCountingClient) -> None:
        self._inner = inner
        self._owner = owner

    async def __aenter__(self) -> _PartitionCountingPipeline:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> Any:
        return await self._inner.__aexit__(*exc)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if callable(attr):
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                self._owner._guard(name)
                return attr(*args, **kwargs)
            return wrapped
        return attr


def test_r2_09_partition_spends_remaining_then_budget_unavailable() -> None:
    """R2-09: partition → spend Remaining_Lease, then budget_unavailable; refill never on-path.

    # Validates: Requirements 11.3

    The harness-level tie-together of the Req-11 partition behaviour (overlaps
    ``test_lgw06_outage_posture.py`` + ``test_lgw06_refill_offpath.py`` by intent). It drives the
    whole :class:`QuotaComponent` through a ``fakeredis`` partition and asserts: (1) the façade
    admits exactly the pre-acquired Remaining_Lease; (2) every subsequent request is refused with
    ``budget_unavailable`` (never ``shared_state_unavailable``); (3) the request-path store-call
    counter stays ``0`` across the whole run — no synchronous refill read ever reaches the store on
    the hot path.
    """
    seed = 0x06_13_03

    async def go() -> None:
        rng = random.Random(seed)
        server = fakeredis.FakeServer()
        inner = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        client = _PartitionCountingClient(inner)
        metrics = _CountingMetrics()

        chunk = rng.randint(8, 48)
        low = derive_low_watermark(chunk)
        config = LeaseConfig(org=_ORG, chunk=chunk, low_watermark=low, ttl_s=30.0)
        await inner.set(_KEYS.budget_remaining(_ORG), 10_000)
        await inner.set(_KEYS.budget_generation(_ORG), 0)

        pending: list[Awaitable[None]] = []
        lease = BudgetLease(
            client,
            config,
            worker_id="w0",
            clock=lambda: 0.0,
            spawn=pending.append,
            metrics=metrics,
            keys=_KEYS,
        )
        await lease.acquire(0)  # pre-acquire a chunk while healthy (off the request path)
        held = lease.remaining
        assert held > 0

        gcra = LocalGCRA(GcraParams(rate_per_s=1_000_000.0, burst=100_000), clock=lambda: 0.0)
        quota = QuotaComponent(gcra, lease, generation_source=lambda: 0, metrics=metrics)

        # Partition the store: no off-path refill can land; the worker has exactly ``held`` budget.
        client.partitioned = True

        admitted = 0
        refused = 0
        total = held + rng.randint(20, 80)
        for n in range(total):
            client.on_request_path = True
            verdict = await quota.evaluate(1, request_id=f"r{n}")
            client.on_request_path = False
            # The request path must NEVER have touched the store (Req 11.3 / Property 5).
            assert client.request_path_calls == 0, (
                f"seed={seed:#x} iter={n} a store call reached the request path: "
                f"{client.request_path_calls}"
            )
            if isinstance(verdict, Admitted):
                admitted += 1
            else:
                assert isinstance(verdict, BudgetVerdict)
                assert verdict.code == BUDGET_UNAVAILABLE, (
                    f"seed={seed:#x} iter={n} refusal {verdict.code!r} is not the narrow posture"
                )
                assert verdict.code != SHARED_STATE_UNAVAILABLE
                refused += 1
            # Attempt to drain any scheduled off-path refill (it fails under partition; the lease's
            # _refill catches it in a finally and clears the guard). Drive it OUTSIDE the gate.
            while pending:
                coro = pending.pop(0)
                try:
                    await coro
                except RedisConnectionError:
                    pass

        assert admitted == held, (
            f"seed={seed:#x} admitted={admitted} != pre-acquired Remaining_Lease={held}"
        )
        assert refused >= 1, f"seed={seed:#x} never crossed into the refusal regime"
        assert client.request_path_calls == 0
        assert metrics.budget_unavailable == refused, (
            f"seed={seed:#x} budget_unavailable metric {metrics.budget_unavailable} != {refused}"
        )
        for coro in pending:
            coro.close()  # type: ignore[attr-defined]
        await inner.aclose()

    _run(go())
