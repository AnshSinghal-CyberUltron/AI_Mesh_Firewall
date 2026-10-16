"""Bounded, instrumented provider connection pool (GW11 C7, R9).

GW11's provider path must hold at most a bounded number of concurrent upstream
connections so one BYOK tenant's burst cannot exhaust a shared worker, and the
contention must be *observable* rather than silently fatal. :class:`ProviderPool`
is that bound: a fixed number of lease slots guarded by an ``asyncio.Semaphore``,
acquired through an async ``lease()`` context manager that applies bounded
queueing with backpressure and records a non-negative ``pool_wait_time`` sample
on every acquisition (R9.2, R9.3).

**The bound is injected, never a literal (R9.1).** The slot count arrives as a
plain ``int`` resolved at the edge wiring site from
``ResourceContract.pool_size(PoolKind.PROVIDER)`` (``runtime/resources.py`` is the
one module permitted a capacity-position literal). This module names no
``maxsize=`` / ``Semaphore(<int>)`` capacity literal of its own: it builds the
semaphore from the *variable* ``bound``, which ``lint/check_capacity_literals.py``
does not flag (that gate flags a literal integer in capacity position, not a
passed-in variable), so the capacity-literal gate stays green.

**Backpressure, not an exception storm (R9.2).** When all ``bound`` slots are
held, ``lease()`` *awaits* a free slot on the semaphore — the standard asyncio
backpressure primitive — rather than raising. A queued acquirer simply waits
until a holder releases; no ``TimeoutError``/``QueueFull``-style exception storm
is produced under exhaustion. The slot is always released on exit, including when
the leased body raises, because the semaphore release is in a ``finally``.

**Wait time is measured on an injected clock (R9.3).** ``pool_wait_time`` is the
elapsed time between asking for a slot and acquiring one, measured with an
injected ``Callable[[], float]`` clock defaulting to ``time.monotonic`` — the
same injected-clock idiom ``admit/codel.py`` and ``admit/killswitch.py`` use, so
a test can advance time deterministically. The sample is non-negative by
construction (``max(0.0, after - before)``), guarding against a non-monotonic
injected clock, and is handed to the injected :class:`WaitMetric` on *every*
acquisition — a free slot acquired instantly records a ``0.0`` sample, never an
absent one.

**No shared mutable module state.** The semaphore is per-instance state held in
``__slots__`` (allowed, like ``admit/codel.py``'s per-owner counters); there is no
module-level mutable. The injected ``WaitMetric`` is the only sink for the
exported wait time, keeping this module a bound + an instrument with no I/O.

**Layering.** ``dispatch`` may import ``runtime``/``domain`` (lower layers), but
this module needs neither: the bound is a plain ``int`` passed in and the metric
is a structural ``Protocol`` declared here, so ``pool.py`` imports only the
standard library. No ``detect``/``plan``/``admit``/``edge`` import.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable

__all__ = (
    "ProviderPool",
    "WaitMetric",
)


@runtime_checkable
class WaitMetric(Protocol):
    """The minimal sink the pool exports its ``pool_wait_time`` through (R9.3).

    Declared structurally (not imported) so ``pool.py`` depends on no sibling or
    lower-layer module: any object with an ``observe_wait(seconds: float) -> None``
    method satisfies it. The gateway_v2 metrics producers (``admit/metrics.py``,
    ``runtime/holdback_metrics.py``) follow the same producer-vs-publisher split —
    the pool is a *producer* that hands each acquisition's wait time to this sink,
    and a publisher (GW14d) owns ``# TYPE`` exposition. ``seconds`` is always a
    non-negative wait sample; a free slot acquired instantly records ``0.0``.
    """

    def observe_wait(self, seconds: float) -> None:
        """Record one ``pool_wait_time`` sample (seconds, non-negative). O(1)."""
        ...


class ProviderPool:
    """Bounded, instrumented provider connection pool (R9).

    Holds at most ``bound`` concurrent leases. ``bound`` is injected (resolved from
    ``ResourceContract.pool_size(PoolKind.PROVIDER)`` at the edge wiring site), so
    this class carries no capacity literal (R9.1). ``lease()`` is an async context
    manager that acquires one slot — awaiting a free one under exhaustion
    (bounded backpressure, never an exception storm — R9.2) — and records a
    non-negative ``pool_wait_time`` sample through the injected :class:`WaitMetric`
    on every acquisition (R9.3). The slot is released on exit even if the leased
    body raises.

    All mutable state is per-instance (the semaphore); there is no module-level
    mutable. The clock is injected (``Callable[[], float]`` defaulting to
    ``time.monotonic``) so the wait time is measured deterministically under test.
    """

    __slots__ = ("_bound", "_clock", "_sem", "_wait_metric")

    def __init__(
        self,
        *,
        bound: int,
        wait_metric: WaitMetric,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build a pool of ``bound`` lease slots.

        ``bound`` is the injected slot count (``pool_size(PoolKind.PROVIDER)``
        resolved upstream); it must be at least one, since a pool of zero slots
        could never admit a lease and would deadlock the first acquirer. The
        ``asyncio.Semaphore`` is built from the *variable* ``bound`` — not a
        literal — so the capacity-literal gate stays green. ``wait_metric`` is the
        injected sink the exported ``pool_wait_time`` is handed to; ``clock`` is the
        injected monotonic clock the wait time is measured on.
        """
        if bound < 1:
            raise ValueError(f"ProviderPool bound must be at least 1, got {bound!r}")
        self._bound = bound
        self._wait_metric = wait_metric
        self._clock = clock
        # Built from the injected `bound` variable (never a literal), so
        # `check_capacity_literals` does not flag it: the gate flags a literal
        # integer in capacity position, not a passed-in variable.
        self._sem = asyncio.Semaphore(bound)

    @property
    def bound(self) -> int:
        """The injected slot count — the maximum concurrent leases (R9.1)."""
        return self._bound

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[None]:
        """Acquire one lease slot, applying bounded backpressure (R9.2, R9.3).

        Awaits a free slot: when all ``bound`` slots are held, the acquirer waits
        on the semaphore (backpressure) rather than raising — no exception storm
        under exhaustion (R9.2). On acquisition the elapsed wait time
        (``after - before`` on the injected clock, floored at ``0.0`` so a
        non-monotonic clock can never produce a negative sample) is handed to the
        injected :class:`WaitMetric` as the exported ``pool_wait_time`` — recorded
        on *every* acquisition, so an instantly-acquired slot records ``0.0`` and
        never an absent sample (R9.3). The slot is released in ``finally`` so a
        raising leased body cannot leak a slot and shrink the live bound.
        """
        started_at = self._clock()
        await self._sem.acquire()
        waited = max(0.0, self._clock() - started_at)
        self._wait_metric.observe_wait(waited)
        try:
            yield None
        finally:
            self._sem.release()
