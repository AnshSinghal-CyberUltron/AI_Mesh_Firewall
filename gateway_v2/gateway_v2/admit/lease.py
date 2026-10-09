"""Store-backed ``BudgetLease`` state machine for the org token budget (R2-09, card GW06).

The org-level token *budget* is one shared pool a worker leases a chunk of and then spends
**locally**, so no request ever performs a synchronous store read to admit against the budget. This
module owns that lease: it acquires a chunk from the shared store key, spends it locally against
requests, refills the chunk **off the request path** at a Low_Watermark, and returns the unspent
remainder on clean shutdown (with a store-clock TTL reclaiming a crashed worker's chunk). The
orthogonal burst/rate limit is ``admit/gcra.py``; the admit/refuse composition is
``admit/quota.py``.

The store client is **injected** (Req 1.5): this module never constructs one, so every
store-touching path is exercised with ``fakeredis`` in the tests. The layer contract (``admit``
imports only ``runtime`` + ``domain``) is respected — the only imports are the ``runtime`` key
layout and the standard library.

Atomicity — WATCH/MULTI, not Lua EVAL
-------------------------------------
The design's acquire/return is "one logical step": a generation check, a pool decrement, a
per-worker lease record, and a TTL, applied atomically. The design expresses that as a single Lua
``EVAL``. The ``fakeredis`` the suite runs against (``fakeredis 2.39``) does **not** implement
``EVAL``/``EVALSHA``, so this module uses the WATCH/MULTI optimistic-transaction equivalent that
``fakeredis`` *does* implement and that a real Valkey/Redis implements identically:

* ``WATCH remaining, generation`` — the transaction aborts (``WatchError``) if either key changed
  between the read and the ``EXEC``, so a concurrent worker's acquire can never interleave with
  this one's decrement. On abort we retry the whole optimistic cycle (bounded by
  ``_MAX_CAS_TRIES``).
* the generation is read **inside** the watch, so a stale generation is detected against the value
  the ``EXEC`` is guarded on, not a value that could have advanced since.
* ``MULTI`` then issues ``DECRBY remaining grant`` + ``SET lease:<worker> grant`` + ``PEXPIRE
  lease:<worker> ttl`` as one queued batch committed by one ``EXEC``.

The invariant the design asks for — generation-check + pool-decrement + lease-set + TTL as one
atomic step that cannot over-grant the pool under concurrent replicas — holds under this equivalent
exactly as it would under the ``EVAL``; the no-replica-multiplication property test
(``test_lgw06_lease_no_multiplication``) drives N instances over one ``fakeredis`` and asserts it.

Purity boundaries
-----------------
``try_spend`` is **synchronous and local**: it decrements the in-memory ``Remaining_Lease`` and, at
the Low_Watermark, *schedules* a refill via the injected ``spawn`` — it never ``await``\\s and
never reads the store (Req 4.2; Property 5). ``acquire`` and ``return_unspent`` are the only
store-touching methods; ``acquire`` runs at construction and from the off-path refill task, never
from ``try_spend``. The only mutable state is the per-instance ``_LeaseState`` carrier — there is
no module-level mutable state, and the config / result types are frozen slotted dataclasses.

Retry-once-on-idempotent-read boundary (R2-14)
----------------------------------------------
Roughly ``1e-4`` of **cross-zone** store round trips hit the ~200 ms minimum TCP RTO, over the
25 ms request-path per-op timeout (``bounded_timeout_s`` on the injected client). The correction
register's R2-14 has two halves:

* **A deployment dependency, not code.** The durable fix is to place the gateways in the store
  primary's zone so a round trip is never cross-zone — that removes the RTO spike at the source.
  This module cannot assert topology; it only documents the dependency.
* **The code-side requirement — retry once on an idempotent read timeout.** An ``Idempotent_Read``
  (a store read with *no side effects*, e.g. a plain ``GET`` of the Budget_Generation) that times
  out is retried **at most once**; a second timeout **surfaces** (Req 8.1, 8.3). A **mutating**
  operation — the atomic WATCH/MULTI acquire/return (``DECRBY``/``SET``/``INCRBY``/``DEL``) — is
  **never** retried on timeout (Req 8.2): a retry could double-apply the decrement or the return,
  so a timeout there surfaces to the caller and the off-path refill reschedules a fresh attempt
  later. ``_read_once_retrying`` is the ONLY retry surface; it wraps a single genuinely idempotent
  read (``read_generation``) and nothing else — never the transaction.

Fail-closed: a surfaced timeout propagates to the caller (the façade's catch-all refuses), so a
persistent store outage never admits — it degrades to ``budget_unavailable`` or a refusal.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import WatchError

from gateway_v2.runtime.store_keys import KEYS, StoreKeys

_T = TypeVar("_T")

__all__ = (
    "BudgetLease",
    "LeaseConfig",
    "QuotaMetricsLike",
    "SpendResult",
)

_STALE_GENERATION = -1
"""Sentinel the atomic acquire returns when the caller's generation is behind the store's."""

_MAX_CAS_TRIES = 16
"""Bound on optimistic-transaction retries before an acquire surfaces contention as a 0 grant.

Not a capacity position: it is the retry budget for the WATCH/MULTI compare-and-set loop (the
``EVAL`` equivalent), so a pathologically contended pool cannot spin the off-path refill task
forever. A real store commits the ``EVAL`` in one shot; this bound exists only because the
optimistic equivalent can lose the race and must give up rather than loop unboundedly.
"""

_IDEMPOTENT_READ_RETRIES = 1
"""R2-14: an Idempotent_Read is retried **at most once** on a store timeout.

Not a capacity position: it is the retry budget for a side-effect-free read (``read_generation``).
``1`` means the read is attempted twice at most — the original plus a single retry — and a second
timeout surfaces (Req 8.1, 8.3). A mutating acquire/return is **never** routed through this and is
never retried on timeout (Req 8.2).
"""

_STORE_TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (
    RedisTimeoutError,
    TimeoutError,
)
"""The timeout signatures a bounded store read can raise.

``redis.exceptions.TimeoutError`` is what redis-py raises when ``socket_timeout`` fires; the builtin
``TimeoutError`` (which ``asyncio.TimeoutError`` aliases on 3.11+, and which ``fakeredis`` /
``asyncio.wait_for`` raise) covers the OS-level ``ETIMEDOUT`` and the event-loop timeout path. A
non-timeout store error is NOT retried — it is not a transient TCP RTO spike, so it surfaces
immediately.
"""


class QuotaMetricsLike(Protocol):
    """The minimal metrics surface ``BudgetLease`` produces into (structural, not a hard dep).

    ``admit/metrics.py`` task 10 builds the concrete ``QuotaMetrics`` producer; defining the surface
    structurally here keeps ``lease.py`` from depending on that later task while still emitting the
    Async_Refill observation the design names. Any object exposing ``observe_async_refill()`` (e.g.
    the real producer, or a tiny counting double in the tests) satisfies it.
    """

    def observe_async_refill(self) -> None:
        """Record that one Async_Refill ran (off the request path). O(1), no I/O."""
        ...


@dataclass(frozen=True, slots=True)
class LeaseConfig:
    """Immutable configuration for one worker's lease on one org's budget.

    ``chunk`` (Lease_Chunk) and ``low_watermark`` are **derived from the ResourceContract** by the
    façade and passed in — never literals here, so the ``admit/`` capacity-literal gate stays green.
    ``ttl_s`` (Lease_TTL) is enforced on the **store's clock** (Req 3.4) so a crashed worker's chunk
    is reclaimed by TTL expiry rather than lost.
    """

    org: str
    chunk: int
    low_watermark: int
    ttl_s: float


@dataclass(slots=True)
class _LeaseState:
    """The ONLY mutable carrier — per instance, not frozen (``remaining`` changes on every spend).

    ``refill_in_flight`` is the single-flight guard: at most one Async_Refill may be in flight, so a
    ``try_spend`` that fires the watermark while a refill is already running schedules nothing new
    and keeps serving from ``remaining`` (Req 4.3).
    """

    remaining: int
    generation: int
    refill_in_flight: bool = False


@dataclass(frozen=True, slots=True)
class SpendResult:
    """The outcome of a local ``try_spend`` — a value, never an HTTP object.

    ``admitted`` is whether the cost was drawn from the Remaining_Lease. ``remaining_after`` is the
    Remaining_Lease after the call (unchanged on a refusal). ``needs_refill`` reports that the call
    left the lease at or below the Low_Watermark (an Async_Refill was scheduled, or one was already
    in flight). ``stale_generation`` reports that the held lease is behind the caller's generation
    and must be re-acquired before any further budget admission (Req 6.3 / 6.4; Property 3) — a
    stale lease is never spent.
    """

    admitted: bool
    remaining_after: int
    needs_refill: bool
    stale_generation: bool


def _as_int(value: object) -> int:
    """Coerce a store answer (bytes / str / int / None) to int; missing or junk reads as 0."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return int(value)
        except ValueError:
            return 0
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


class BudgetLease:
    """One worker's lease on one org's shared token budget. Store client injected (Req 1.5).

    Holds the Remaining_Lease in memory, acquires chunks from the shared ``budget_remaining`` pool,
    spends them locally, and refills off the request path. See the module docstring for the
    WATCH/MULTI atomicity equivalent and the purity boundaries.
    """

    __slots__ = (
        "_client",
        "_clock",
        "_config",
        "_keys",
        "_metrics",
        "_spawn",
        "_state",
        "_worker_id",
    )

    def __init__(
        self,
        client: Any,
        config: LeaseConfig,
        *,
        worker_id: str,
        clock: Callable[[], float] = time.monotonic,
        spawn: Callable[[Awaitable[None]], None],
        metrics: QuotaMetricsLike,
        keys: StoreKeys = KEYS,
    ) -> None:
        if not worker_id:
            msg = "worker_id must be a stable non-empty id (the lease key is per worker)"
            raise ValueError(msg)
        if config.chunk < 1:
            msg = f"chunk must be a positive contract-derived size, got {config.chunk!r}"
            raise ValueError(msg)
        if config.low_watermark < 0 or config.low_watermark >= config.chunk:
            msg = (
                "low_watermark must be in [0, chunk) and derived from the chunk, "
                f"got {config.low_watermark!r} for chunk {config.chunk!r}"
            )
            raise ValueError(msg)
        if config.ttl_s <= 0.0:
            msg = f"ttl_s (Lease_TTL) must be positive, got {config.ttl_s!r}"
            raise ValueError(msg)
        self._client = client
        self._config = config
        self._worker_id = worker_id
        self._clock = clock
        self._spawn = spawn
        self._metrics = metrics
        self._keys = keys
        # Starts empty and under the caller's generation: nothing is held until the first acquire.
        self._state = _LeaseState(remaining=0, generation=0, refill_in_flight=False)

    @property
    def config(self) -> LeaseConfig:
        return self._config

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def remaining(self) -> int:
        """The Remaining_Lease held locally (O(1), no store read)."""
        return self._state.remaining

    @property
    def generation(self) -> int:
        """The Budget_Generation the held lease was acquired under (O(1), no store read)."""
        return self._state.generation

    @property
    def refill_in_flight(self) -> bool:
        """Whether an Async_Refill is currently in flight (the single-flight guard)."""
        return self._state.refill_in_flight

    # --- store-touching: idempotent read with retry-once boundary (R2-14) --------------------- #

    async def _read_once_retrying(
        self,
        coro_factory: Callable[[], Awaitable[_T]],
    ) -> _T:
        """Run a single **idempotent** store read, retrying **at most once** on a timeout (R2-14).

        ``coro_factory`` must build a fresh awaitable for one *side-effect-free* read (a plain
        ``GET``), so retrying it cannot double-apply anything. On a store timeout
        (``_STORE_TIMEOUT_ERRORS``) the read is retried exactly once; a **second** timeout is
        re-raised so it surfaces to the caller (Req 8.1, 8.3). A non-timeout error is never retried
        — only the transient cross-zone TCP RTO spike is (Req 8.1). This is the ONLY retry surface
        in the module: the mutating WATCH/MULTI acquire/return are **never** routed through it
        (Req 8.2), so a timeout on a mutating op surfaces and the off-path refill reschedules.
        """
        attempts_left = _IDEMPOTENT_READ_RETRIES
        while True:
            try:
                return await coro_factory()
            except _STORE_TIMEOUT_ERRORS:
                if attempts_left <= 0:
                    # The single retry also timed out: surface rather than retry again (Req 8.3).
                    raise
                attempts_left -= 1

    async def read_generation(self) -> int:
        """Read the store's current Budget_Generation. Idempotent (no side effects), retry-once.

        A plain ``GET`` of the generation key, routed through ``_read_once_retrying`` so a transient
        cross-zone timeout is retried once (Req 8.1) and a second timeout surfaces (Req 8.3). This
        is the lease's representative Idempotent_Read surface — a generation pre-check the façade or
        the refill path can use to re-read the generation **without** entering the mutating
        transaction. It never decrements the pool, sets a lease, or writes anything.
        """
        generation_key = self._keys.budget_generation(self._config.org)
        return _as_int(await self._read_once_retrying(lambda: self._client.get(generation_key)))

    # --- store-touching: acquire -------------------------------------------------------------- #

    async def acquire(self, generation: int) -> bool:
        """Acquire one Lease_Chunk from the shared pool under ``generation``. One logical step.

        Returns ``True`` when a positive grant was added to the Remaining_Lease, ``False`` when the
        pool was empty (grant ``0``). On a **stale** generation the store reports its current
        generation; this method adopts it and re-acquires under it exactly once (Req 6.4), so a
        config change that advances the generation mid-flight does not strand the worker — the
        re-acquire draws from the pool under the current generation (Property 3).

        Store-touching (WATCH/MULTI compare-and-set; see the module docstring). Called at
        construction and from the off-path refill task only — **never** from ``try_spend``.
        """
        grant, current_gen = await self._acquire_once(generation)
        if grant == _STALE_GENERATION:
            # The caller's generation is behind the store's: adopt the store's and re-acquire under
            # it once. A stale lease is never spent (the façade re-reads the generation on a stale
            # SpendResult; this path keeps acquire self-correcting for the refill task).
            grant, current_gen = await self._acquire_once(current_gen)
            if grant == _STALE_GENERATION:
                # Still stale (the generation advanced again between the two reads): do not spend.
                return False
        self._state.generation = current_gen
        if grant <= 0:
            return False
        self._state.remaining += grant
        return True

    async def _acquire_once(self, want_gen: int) -> tuple[int, int]:
        """One atomic acquire attempt. Returns ``(grant, current_generation)``.

        ``grant`` is ``_STALE_GENERATION`` (``-1``) when ``want_gen`` is behind the store's
        generation, ``0`` when the pool is empty (never over-grant), else ``min(pool, chunk)``. The
        generation read, the ``min(pool, chunk)`` decrement, the per-worker lease ``SET`` and the
        store-clock TTL are committed as one ``MULTI``/``EXEC`` guarded by ``WATCH`` on the pool and
        generation keys — the WATCH/MULTI equivalent of the design's single ``EVAL``.
        """
        remaining_key = self._keys.budget_remaining(self._config.org)
        generation_key = self._keys.budget_generation(self._config.org)
        lease_key = self._keys.budget_lease(self._config.org, self._worker_id)
        ttl_ms = max(1, round(self._config.ttl_s * 1000))

        for _ in range(_MAX_CAS_TRIES):
            async with self._client.pipeline() as pipe:
                try:
                    await pipe.watch(remaining_key, generation_key)
                    current_gen = _as_int(await pipe.get(generation_key))
                    if current_gen != want_gen:
                        await pipe.unwatch()
                        return _STALE_GENERATION, current_gen
                    pool = _as_int(await pipe.get(remaining_key))
                    grant = min(pool, self._config.chunk)
                    if grant <= 0:
                        await pipe.unwatch()
                        return 0, current_gen
                    pipe.multi()
                    pipe.decrby(remaining_key, grant)
                    pipe.set(lease_key, grant)
                    pipe.pexpire(lease_key, ttl_ms)
                    await pipe.execute()
                except WatchError:
                    # A concurrent worker touched the pool or generation between the WATCH and the
                    # EXEC: retry the whole optimistic cycle. This is the only path a real EVAL
                    # would not have, and it never over-grants — the retry re-reads the pool.
                    #
                    # NOTE (R2-14 / Req 8.2): only a WatchError retries here. A store **timeout**
                    # inside this mutating WATCH/MULTI is NOT caught and propagates out of
                    # ``acquire`` unretried — a retry could double-apply the DECRBY/SET. The timeout
                    # surfaces; the off-path refill reschedules a fresh attempt later.
                    continue
                else:
                    return grant, current_gen
        # Lost the compare-and-set race _MAX_CAS_TRIES times under heavy contention: report an empty
        # grant rather than spin. The next watermark reschedules a refill (fail-closed, never over).
        return 0, want_gen

    # --- local, synchronous: try_spend ------------------------------------------------------- #

    def try_spend(self, cost: int, generation: int) -> SpendResult:
        """Spend ``cost`` from the Remaining_Lease locally. Synchronous: no ``await``, no store I/O.

        * A **stale** held generation (behind ``generation``) is unspendable: return a
          ``stale_generation`` result; the caller re-acquires under the current generation before
          any further budget admission (Req 6.3; Property 3). The lease is never spent stale.
        * ``remaining >= cost`` → admit by decrementing locally. When the result is at or below the
          Low_Watermark, schedule an Async_Refill via the injected ``spawn`` under the single-flight
          guard (Req 4.1): if a refill is already in flight, schedule nothing new and keep serving
          (Req 4.3). The refill runs **off the request path** (Req 4.2; Property 5).
        * ``remaining < cost`` → do **not** admit from this call. The façade decides between
          ``budget_unavailable`` (pool + remaining both exhausted) and waiting on an in-flight
          refill; ``try_spend`` never acquires (Req 4.2).
        """
        state = self._state
        if state.generation < generation:
            return SpendResult(
                admitted=False,
                remaining_after=state.remaining,
                needs_refill=False,
                stale_generation=True,
            )
        if state.remaining < cost:
            # Not enough held budget. Nudge a refill if we are at/below the watermark so the next
            # attempt can be served; the caller owns the refuse-vs-wait decision.
            needs_refill = self._maybe_schedule_refill()
            return SpendResult(
                admitted=False,
                remaining_after=state.remaining,
                needs_refill=needs_refill,
                stale_generation=False,
            )
        state.remaining -= cost
        needs_refill = self._maybe_schedule_refill()
        return SpendResult(
            admitted=True,
            remaining_after=state.remaining,
            needs_refill=needs_refill,
            stale_generation=False,
        )

    def _maybe_schedule_refill(self) -> bool:
        """Schedule a single-flight Async_Refill iff at/below the watermark. Returns "needs refill".

        Returns ``True`` when the Remaining_Lease is at or below the Low_Watermark (whether a new
        refill was scheduled or one was already in flight). Schedules at most one refill: the
        ``refill_in_flight`` guard is set *before* ``spawn`` so a burst of watermark-crossing spends
        cannot launch a second refill (Req 4.3). Never ``await``\\s — ``spawn`` hands the coroutine
        to the injected scheduler and returns immediately (off the request path).
        """
        state = self._state
        if state.remaining > self._config.low_watermark:
            return False
        if not state.refill_in_flight:
            state.refill_in_flight = True
            self._spawn(self._refill())
        return True

    async def _refill(self) -> None:
        """Off-path refill task: acquire a new chunk, add it, clear the single-flight guard.

        Observes the Async_Refill metric. On any failure the guard is still cleared (``finally``) so
        the next watermark-crossing spend reschedules a fresh refill (Req 4.4) — a failed refill
        leaves the worker serving from the Remaining_Lease, never wedged with the guard stuck set.
        """
        try:
            self._metrics.observe_async_refill()
            await self.acquire(self._state.generation)
        finally:
            self._state.refill_in_flight = False

    # --- store-touching: return_unspent ------------------------------------------------------ #

    async def return_unspent(self) -> None:
        """Return the unspent Remaining_Lease to the pool and delete the lease record. One step.

        Called on **clean** shutdown: a single atomic WATCH/MULTI (the ``EVAL`` equivalent) adds
        the unspent remainder back to ``budget_remaining`` and ``DEL``\\s the per-worker lease
        record, so the budget is immediately available to other workers. A **crashed** worker never
        runs this; its ``budget_lease:<worker>`` key expires on the store's clock (the Lease_TTL set
        at acquire) and the expired chunk is reclaimed, so budget is never permanently lost (Req
        3.5; Property 2). Idempotent: a zero Remaining_Lease still deletes the record and adds none.
        """
        state = self._state
        amount = state.remaining
        remaining_key = self._keys.budget_remaining(self._config.org)
        lease_key = self._keys.budget_lease(self._config.org, self._worker_id)
        for _ in range(_MAX_CAS_TRIES):
            async with self._client.pipeline() as pipe:
                try:
                    await pipe.watch(remaining_key, lease_key)
                    pipe.multi()
                    if amount > 0:
                        pipe.incrby(remaining_key, amount)
                    pipe.delete(lease_key)
                    await pipe.execute()
                except WatchError:
                    # Only contention retries. A store timeout on this mutating return propagates
                    # unretried (R2-14 / Req 8.2): a retried INCRBY could double-return the budget.
                    continue
                else:
                    break
        # Local remainder is now owned by the pool (or the record is gone): drop it locally so a
        # double return cannot add it twice.
        state.remaining = 0
