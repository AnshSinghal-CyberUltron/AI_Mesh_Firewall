"""The GC instrument must measure without disturbing — task 7."""
from __future__ import annotations

import gc
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai_mesh_gateway"))

from ai_mesh_gateway import gc_monitor  # noqa: E402


def test_disabled_by_default_registers_nothing(monkeypatch):
    """R4: a diagnostic that always runs is a permanent cost for an occasional question."""
    monkeypatch.delenv(gc_monitor._ENV, raising=False)
    before = len(gc.callbacks)
    assert gc_monitor.init() is False
    assert len(gc.callbacks) == before
    assert gc_monitor.mark() is None
    assert gc_monitor.pause_ms_since(0.0) is None


def test_enabled_measures_a_real_collection(monkeypatch):
    mon = gc_monitor._GCMonitor()
    gc.callbacks.append(mon._callback)
    mon.enabled = True
    try:
        before = mon.total_pause_ms
        gen2_before = mon.counts[2]
        junk = [[object() for _ in range(50)] for _ in range(200)]
        gc.collect()                       # a real, full collection
        del junk
        assert mon.counts[2] > gen2_before, "gen2 collection was not counted"
        assert mon.total_pause_ms > before, "a real collection recorded no pause time"
    finally:
        gc.callbacks.remove(mon._callback)


def test_callback_never_raises_on_a_malformed_info():
    """It runs inside every collection; CPython swallows exceptions, so a raise here is
    wasted work that is invisible."""
    mon = gc_monitor._GCMonitor()
    mon._callback("start", {})
    mon._callback("stop", {})                    # no "generation" key
    mon._callback("stop", None)                  # not a dict at all
    mon._callback("nonsense", {"generation": 9})  # unknown phase, out-of-range gen


def test_pause_since_is_a_subtraction_not_a_residual():
    mon = gc_monitor._GCMonitor()
    mon.enabled = True
    orig = gc_monitor.MONITOR
    gc_monitor.MONITOR = mon
    try:
        m = gc_monitor.mark()
        assert m == 0.0
        mon.total_pause_ms = 37.5
        assert gc_monitor.pause_ms_since(m) == 37.5
        # Never negative, even if a mark is somehow ahead of the counter.
        assert gc_monitor.pause_ms_since(99.0) == 0.0
    finally:
        gc_monitor.MONITOR = orig


def test_snapshot_reports_every_generation():
    mon = gc_monitor._GCMonitor()
    snap = mon.snapshot()
    for k in ("gc_pause_total_ms", "gc_gen0", "gc_gen1", "gc_gen2"):
        assert k in snap, f"snapshot lost {k}; 'GC took 50 ms' without the generation "\
                          f"cannot direct a fix (R3)"
