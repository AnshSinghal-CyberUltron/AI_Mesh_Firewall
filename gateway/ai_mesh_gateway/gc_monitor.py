"""Measure GC pauses, so the tail can be attributed instead of reasoned about — task 7.

The tail excess lands on a different stage nearly every request and is sized independently
of what that stage does — 27% of it lands on `model_output`, whose duration is the stub's
own fixed pacing. `PipelineStageTimer.mark_segment_end` charges all elapsed time since the
previous boundary to the stage being closed, so a process-wide pause is billed to whichever
stage happens to be open. That is the shape of the interpreter stopping.

GC is the leading candidate. Five hypotheses have already been refuted, four of them only
after a rebuild and a sweep, so this one is measured first.

`total_pause_ms` is monotonic for the process lifetime, which makes per-request
attribution a subtraction rather than a residual — the distinction that produced two
corrections earlier in this work.
"""
from __future__ import annotations

import gc
import os
import time

_ENV = "GATEWAY_GC_INSTRUMENTATION"


class _GCMonitor:
    """O(1), allocation-free on the hot path, and it can never raise.

    The callback runs inside every collection: anything expensive here is paid on every
    gen0 pass, and an exception is swallowed by CPython so the work would be wasted
    silently.
    """

    __slots__ = ("total_pause_ms", "counts", "_t0", "enabled")

    def __init__(self) -> None:
        self.total_pause_ms: float = 0.0
        self.counts: dict[int, int] = {0: 0, 1: 0, 2: 0}
        self._t0: float = 0.0
        self.enabled: bool = False

    def _callback(self, phase: str, info: dict) -> None:
        try:
            if phase == "start":
                self._t0 = time.perf_counter()
            elif self._t0:
                self.total_pause_ms += (time.perf_counter() - self._t0) * 1000.0
                g = info.get("generation", 0)
                if g in self.counts:
                    self.counts[g] += 1
        except Exception:  # noqa: BLE001 — a diagnostic must never disturb collection
            pass

    def enable(self) -> bool:
        if self.enabled:
            return True
        gc.callbacks.append(self._callback)
        self.enabled = True
        return True

    def snapshot(self) -> dict[str, float]:
        return {
            "gc_pause_total_ms": round(self.total_pause_ms, 2),
            "gc_gen0": self.counts[0],
            "gc_gen1": self.counts[1],
            "gc_gen2": self.counts[2],
        }


MONITOR = _GCMonitor()


def instrumentation_enabled() -> bool:
    """Off by default (R4): a diagnostic that always runs is a permanent cost."""
    return (os.environ.get(_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def init() -> bool:
    """Register the callback if enabled. Idempotent."""
    if not instrumentation_enabled():
        return False
    return MONITOR.enable()


def pause_ms_since(mark: float | None) -> float | None:
    """GC pause time that elapsed since ``mark``.

    A subtraction of two monotonic readings — what actually happened during the request,
    not what is left over after subtracting everything else.
    """
    if mark is None or not MONITOR.enabled:
        return None
    return round(max(0.0, MONITOR.total_pause_ms - mark), 2)


def mark() -> float | None:
    return MONITOR.total_pause_ms if MONITOR.enabled else None
