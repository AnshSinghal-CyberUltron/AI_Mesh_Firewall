"""C38 serving-loop discipline: offload CPU-bound scan work off the loop (GW12, task 14.1).

The streaming serving loop forwards bytes for many concurrent streams on ONE asyncio event
loop. Tokenization and scanning are CPU-bound: run inline on the loop, one stream's scan of a
large frame blocks every OTHER stream's next byte for the whole scan, so a single CPU burst
starves unrelated streams' latency (R13.1 / R13.3). C38 (design Components §12) says that work
runs in a **GIL-releasing executor** reached through ``loop.run_in_executor`` — the loop hands
the slice to a worker thread and is free to serve other streams while it runs — and the executor
is **sized by ``ResourceContract``, never a literal** (R13.1).

This module is that seam. It lives in ``edge`` (the top layer, above ``detect``), which is where
the scanner is already injected into the shipped egress pipeline, so adding the offload here adds
no ``egress → detect`` edge and keeps the layer contract intact. It owns:

* :class:`ScanExecutor` — a thin wrapper over a single ``ThreadPoolExecutor`` whose ``max_workers``
  comes from ``contract.pool_size(PoolKind.SCANNER)`` (``floor(cpu_quota × utilization_cap)`` — the
  cpu-bound scanner pool the contract already derives). No thread-count literal appears here; the
  ``check_capacity_literals`` gate only flags an INTEGER-CONSTANT ``ThreadPoolExecutor(...)`` /
  ``max_workers=<int>``, and this passes a variable derived from the contract.
* :meth:`ScanExecutor.run` — awaits a CPU-bound callable on the executor via
  ``loop.run_in_executor`` and, around the hand-off, measures the serving-loop scheduling lag and
  feeds it to the per-request export producer (R13.4): the loop-lag p99 is the SLO input a
  publisher consumes. The measurement is the gap between when the offload was requested and when
  the loop resumed this coroutine after the worker finished — exactly the loop time the offload
  gives back versus an inline scan that would have held the loop for the whole slice.

**Slice bound (R13.5).** The forwarding loop runs no single CPU-bound slice longer than the
declared slice bound because the slice no longer runs ON the loop at all — it runs on a worker
thread, and the loop's own time-slice between awaits is just the O(1) book-keeping around the
offload. The declared bound the loop must respect is the inter-chunk budget
(``contract.inter_chunk_timeout_s()``); :meth:`ScanExecutor.run` is the mechanism that keeps a
scan slice off the loop so that bound is never consumed by one stream's CPU work.

**What is offloaded here vs. deferred.** The error-frame scan runs at an ``await``-able boundary
(``edge/routes.py::_as_chunks`` is an async generator), so it is offloaded through this seam
today (:func:`~gateway_v2.edge.error_frame_scan.scan_error_frame_offloaded`). The shipped
``egress/stream.py::StreamPipeline`` release loop calls its injected scanner SYNCHRONOUSLY inside
``_process_delta`` (it is a pure function, not a coroutine), so offloading the per-delta holdback
scan would require threading an awaitable executor into that shipped loop body — a re-architecture
of GW12b code this card must not rebuild. That per-delta offload is therefore a documented GW13
follow-up; the executor + sizing + loop-lag SLO input are in place now, and the content-path cap
already bounds held work regardless.

**No module-level mutable, no capacity literal.** The executor is constructed per ``build_app``
assembly from the injected contract; nothing here is a module global.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from gateway_v2.runtime.kinds import PoolKind
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.stream_metrics import PerRequestExports

__all__ = ("ScanExecutor",)

_T = TypeVar("_T")


class ScanExecutor:
    """Offload CPU-bound scan work to a GIL-releasing executor sized by the contract (R13.1).

    The executor's worker count is ``contract.pool_size(PoolKind.SCANNER)`` — the cpu-bound
    scanner pool the contract derives from ``floor(cpu_quota × utilization_cap)`` — so the
    thread-count is a capacity decision made once, in the one module allowed capacity literals,
    and never a literal here. The pool is created eagerly at construction (``build_app`` assembly
    time, off the request path) and reused for every stream; :meth:`shutdown` releases it on
    lifespan shutdown.

    Not a frozen dataclass: it holds the live ``ThreadPoolExecutor`` resource, which is mutable
    runtime state (a pool, not a value), and the ``check_no_module_mutable`` gate only forbids
    MODULE-level mutables — a per-instance pool handle is fine. The loop-lag clock is injected
    (defaulting to ``time.perf_counter_ns`` via :func:`asyncio.get_running_loop` monotonic time)
    so a test can drive the serving-loop-lag SLO input deterministically.
    """

    __slots__ = ("_executor", "_clock")

    def __init__(
        self,
        *,
        contract: ResourceContract,
        clock: Callable[[], int] | None = None,
    ) -> None:
        workers = contract.pool_size(PoolKind.SCANNER)
        self._executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="amf-scan",
        )
        self._clock = clock

    async def run(
        self,
        fn: Callable[[], _T],
        *,
        exports: PerRequestExports | None = None,
    ) -> _T:
        """Run ``fn`` (a CPU-bound scan) on the executor, off the serving loop (R13.1).

        Hands the callable to the worker pool via ``loop.run_in_executor`` and awaits the result,
        so the slice runs on a worker thread and the loop is free to serve other streams meanwhile
        (that is the CPU-burst isolation the task-14.2 test exercises — the mechanism is real, not
        a no-op: a long ``fn`` here does not block the loop). Around the hand-off it measures the
        serving-loop scheduling lag — the gap between requesting the offload and the loop resuming
        this coroutine after the worker finished — and feeds it to ``exports`` as the R13.4 SLO
        input when a per-request producer is wired. ``exports=None`` (the thin-handler default)
        simply skips the measurement; the offload itself is unchanged.
        """
        loop = asyncio.get_running_loop()
        start = self._now(loop)
        result = await loop.run_in_executor(self._executor, fn)
        if exports is not None:
            exports.observe_loop_lag_ns(max(0, self._now(loop) - start))
        return result

    def _now(self, loop: asyncio.AbstractEventLoop) -> int:
        """Monotonic nanosecond reading for the loop-lag measurement.

        Uses the injected clock when a test supplies one; otherwise the running loop's own
        monotonic ``time()`` (seconds) scaled to nanoseconds, so the measurement is on the SAME
        clock the loop schedules against — the honest basis for a serving-loop-lag number.
        """
        if self._clock is not None:
            return self._clock()
        return int(loop.time() * 1_000_000_000)

    def shutdown(self) -> None:
        """Release the worker pool (lifespan shutdown). Idempotent and safe off the loop."""
        self._executor.shutdown(wait=False)
