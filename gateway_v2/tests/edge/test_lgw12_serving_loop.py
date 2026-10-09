# Feature: sse-egress-pipeline
# Validates: Requirements 13.3, 13.1, 13.4
"""C38 CPU-burst isolation test for ``gateway_v2.edge.executor.ScanExecutor`` (GW12, task 14.2).

**Requirement 13.3 (+ 13.1, 13.4).** The streaming serving loop forwards bytes for many concurrent
streams on ONE asyncio event loop. Tokenization and scanning are CPU-bound: run inline on the loop,
one stream's scan of a large frame blocks every OTHER stream's next byte for the whole slice, so a
single CPU burst starves unrelated streams' latency (R13.1 / R13.3). C38 (design Components §12)
offloads that work through :meth:`ScanExecutor.run` → ``loop.run_in_executor`` on a GIL-releasing
worker pool **sized by the ``ResourceContract``** (``pool_size(PoolKind.SCANNER)``), so the loop
is free to serve other streams while a slice runs. This test proves the mechanism is real, not a
no-op: a burst offloaded via :meth:`ScanExecutor.run` does NOT block the loop (a concurrent
"ticker" coroutine keeps making progress), whereas running the SAME burst inline on the loop does
block it (the ticker makes essentially no progress for the burst's duration).

**Why this is deterministic rather than flaky.** The "CPU slice" is modelled by ``time.sleep`` on
the worker thread: a thread sleep RELEASES the GIL, which is exactly what a ``run_in_executor``
offload of real CPU-bound scan work buys back for the loop (the loop regains control the moment the
slice yields). The assertions are QUALITATIVE isolation claims with generous tolerances, never
tight numeric latency bounds:

* OFFLOADED: the ticker must advance by at least a few ticks while the burst runs — the loop was
  free. The burst duration is tens of ms and the tick cadence is sub-millisecond, so a healthy loop
  racks up hundreds of ticks; asserting "at least a handful" tolerates any CI scheduling jitter.
* INLINE: the ticker is awaited to a quiescent baseline, then the burst runs INLINE on the loop
  (a direct call, no executor) with no intervening ``await``, so the loop cannot schedule the
  ticker at all during it — we assert the inline burst advanced the ticker by far fewer ticks than
  the offloaded burst. This is a direction (offloaded ≫ inline), not a wall-clock threshold.

**R13.4 SLO-input wiring.** The same test asserts :meth:`ScanExecutor.run` observes serving-loop
lag into a :class:`~gateway_v2.runtime.stream_metrics.PerRequestExports` when one is passed — the
snapshot's ``loop_lag_ms`` histogram gains a sample per offload — confirming the loop-lag SLO input
(R13.4) is really wired, not declared.

Async paths run through ``asyncio.run`` (house idiom); no ``hypothesis``. The executor is built
from a real ``ResourceContract`` via ``from_signals`` with signals chosen so
``pool_size(PoolKind.SCANNER) >= 2`` (``floor(cpu_quota * utilization_cap)`` =
``floor(4.0 * 0.75)`` = 3), so the offloaded burst and the ticker genuinely occupy different
threads. ``ScanExecutor.shutdown()`` is called in a ``finally`` so the worker pool never leaks.
"""

from __future__ import annotations

import asyncio
import time

from gateway_v2.edge.executor import ScanExecutor
from gateway_v2.runtime.kinds import HardwareSignals, PoolKind
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.stream_metrics import PerRequestExports

# The offloaded "CPU slice": long enough to measure, short enough to keep the whole test well
# under a second. A thread sleep releases the GIL, modelling the CPU time a real scan would hold
# were it NOT offloaded.
_BURST_S = 0.05  # 50 ms

# Ticker cadence: a bare ``asyncio.sleep(0)`` yields to the loop as fast as it can reschedule, so a
# free loop advances the counter hundreds of times across a 50 ms burst. We assert only a small
# floor so the test is immune to CI scheduling jitter.
_TICKER_YIELD_S = 0.0

# Qualitative isolation thresholds (NOT latency bounds). "A few ticks" is the floor the offloaded
# loop must clear; the inline burst must advance the ticker by strictly fewer than this to prove
# the loop was blocked. Generous on both sides.
_MIN_OFFLOADED_TICKS = 3


def _contract() -> ResourceContract:
    """A serviceable contract whose SCANNER pool has >= 2 worker threads.

    ``pool_size(PoolKind.SCANNER)`` is ``floor(cpu_quota * utilization_cap)`` = ``floor(4.0 *
    0.75)`` = 3, so the executor runs an offloaded burst on one worker while leaving spare workers
    free — the same ``from_signals`` fixture shape the other LGW12 edge tests use.
    """
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 * 1024 * 1024,
    )


def _burst() -> int:
    """A CPU-slice stand-in: hold a GIL-releasing boundary for a measurable interval.

    ``time.sleep`` releases the GIL on the worker thread, which is precisely what offloading a real
    CPU-bound scan via ``run_in_executor`` gives the loop back: control the moment the slice yields.
    Returns a sentinel so the executor's generic result plumbing is exercised end to end.
    """
    time.sleep(_BURST_S)
    return 1


class _Ticker:
    """A concurrent coroutine that records serving-loop progress.

    Increments ``count`` on every scheduling turn and keeps running until ``stop`` is set. If the
    loop is free, ``count`` climbs continuously; if the loop is blocked (an inline CPU slice), the
    counter cannot advance until the loop regains control. The max inter-tick gap is recorded too,
    so a blocked interval is visible as a single large gap rather than only as a low total.
    """

    __slots__ = ("count", "stop", "_last", "max_gap_s")

    def __init__(self) -> None:
        self.count = 0
        self.stop = asyncio.Event()
        self._last = 0.0
        self.max_gap_s = 0.0

    async def run(self) -> None:
        self._last = asyncio.get_running_loop().time()
        while not self.stop.is_set():
            now = asyncio.get_running_loop().time()
            self.max_gap_s = max(self.max_gap_s, now - self._last)
            self._last = now
            self.count += 1
            await asyncio.sleep(_TICKER_YIELD_S)


async def _run_offloaded(executor: ScanExecutor, exports: PerRequestExports) -> int:
    """Run the burst via the executor while a ticker records loop progress; return ticks advanced.

    The ticker is started as a concurrent task, allowed to reach a running baseline, then the burst
    is offloaded via :meth:`ScanExecutor.run`. Because the slice runs on a worker thread, the loop
    stays free and the ticker keeps advancing; we return how many ticks it logged across the burst.
    """
    ticker = _Ticker()
    ticker_task = asyncio.ensure_future(ticker.run())
    # Let the ticker reach a running baseline before timing the burst window.
    await asyncio.sleep(0)
    before = ticker.count
    result = await executor.run(_burst, exports=exports)
    advanced = ticker.count - before
    ticker.stop.set()
    await ticker_task
    assert result == 1
    return advanced


async def _run_inline() -> int:
    """Run the SAME burst INLINE on the loop (no executor); return how few ticks advanced.

    The ticker reaches a baseline, then the burst runs as a direct synchronous call with no
    intervening ``await`` — so the loop cannot schedule the ticker for the whole slice. The returned
    tick count is the loop's progress WHILE blocked: it should be far below the offloaded case.
    """
    ticker = _Ticker()
    ticker_task = asyncio.ensure_future(ticker.run())
    await asyncio.sleep(0)
    before = ticker.count
    _burst()  # inline on the loop — blocks every other coroutine for the whole slice.
    advanced = ticker.count - before
    ticker.stop.set()
    await ticker_task
    return advanced


def test_offloaded_burst_does_not_block_the_loop_but_inline_does() -> None:
    """Offloading a burst leaves the loop free; the same burst run inline blocks it (R13.3).

    The qualitative isolation claim of R13.3: the offloaded ticker advances by at least a few ticks
    (the loop served it throughout the slice), whereas the inline ticker advances by strictly fewer
    (the loop was blocked for the whole slice). This is a direction, not a wall-clock bound, so CI
    scheduling jitter cannot flip it.
    """
    executor = ScanExecutor(contract=_contract())
    exports = PerRequestExports()
    try:
        offloaded_ticks = asyncio.run(_run_offloaded(executor, exports))
        inline_ticks = asyncio.run(_run_inline())
    finally:
        executor.shutdown()

    assert offloaded_ticks >= _MIN_OFFLOADED_TICKS, (
        f"offloaded burst let the ticker advance only {offloaded_ticks} ticks "
        f"(< {_MIN_OFFLOADED_TICKS}): the loop appears to have been blocked, so the offload is "
        "not isolating CPU work off the serving loop"
    )
    assert inline_ticks < offloaded_ticks, (
        f"inline burst advanced the ticker {inline_ticks} ticks but the offloaded burst advanced "
        f"{offloaded_ticks}: an inline CPU slice must starve the loop MORE than an offloaded one"
    )


def test_run_observes_loop_lag_into_exports() -> None:
    """``ScanExecutor.run`` feeds serving-loop lag into ``PerRequestExports`` (R13.4 SLO input).

    Each offload records one loop-lag sample, so after N offloads the snapshot's ``loop_lag_ms``
    histogram carries N samples — confirming the SLO-input wiring is real, not declared. ``run`` is
    also called with ``exports=None`` to confirm the thin-handler default simply skips the
    measurement without error.
    """
    executor = ScanExecutor(contract=_contract())
    exports = PerRequestExports()

    async def _drive() -> None:
        # exports=None: the default path must offload fine and record nothing.
        assert await executor.run(_burst) == 1
        assert exports.snapshot().loop_lag_ms.count == 0
        # Three offloads with the producer wired: three loop-lag samples.
        for _ in range(3):
            assert await executor.run(_burst, exports=exports) == 1

    try:
        asyncio.run(_drive())
    finally:
        executor.shutdown()

    assert exports.snapshot().loop_lag_ms.count == 3


def test_scanner_pool_has_at_least_two_workers() -> None:
    """The fixture's SCANNER pool is >= 2, so offload and ticker occupy different threads (R13.1).

    This guards the test's own premise: ``pool_size(PoolKind.SCANNER)`` must be at least two for an
    offloaded burst to run concurrently with the loop's other work. ``floor(4.0 * 0.75) == 3``.
    """
    assert _contract().pool_size(PoolKind.SCANNER) >= 2
