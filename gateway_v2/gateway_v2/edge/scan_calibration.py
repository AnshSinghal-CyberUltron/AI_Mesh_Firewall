"""Measure this host's per-slot scan cost once, so tool bounds follow the hardware (GW34).

`max_scanned_slots` is "how many detector calls fit in the scan share of the p99 target". A
constant per-call cost measured on one machine is wrong on every other: on GitHub's runners the
declared 200 us was ~280 us, so the derived bound overran its budget by 40%.

How the cost is decided (`scan_sizing`):

1. `AMF_SCAN_SLOT_COST_US`, when set, is the cost (microseconds per slot, margin included). The
   serving parent sets it after its own calibration (`export_for_workers`), so every worker
   sizes the same bound from one measurement taken before any worker starts. An operator may
   also pin it. A non-numeric, non-finite or non-positive value refuses to start.
2. Otherwise the cost is measured: each of N passes scans the worst-case slots until one scan
   window has elapsed (capped by `_SCAN_CALIBRATION_MAX_PASS_S`), and the median pass x margin
   is kept. A bound below the floor is re-measured up to `_SCAN_CALIBRATION_RETRIES` times and
   the cheapest result kept (the least-contended sample).
3. A bound still below the floor is raised to it and the clamp is logged: every slot is still
   scanned, so only the latency budget is at stake, and a refusal here would turn a busy host
   into one that rejects realistic tool requests for the life of the worker.

The detectors timed are `BASE_DETECTORS`, the set `DeterministicFindings` serves with. Customer
(GW26) conditions are not in the per-slot cost; their wall time is bounded by the regex engine's
own budget (F1a-1).
"""
from __future__ import annotations

import functools
import logging
import math
import os
import statistics
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.deterministic.detectors import BASE_DETECTORS
from gateway_v2.detect.guard.backend import Budget
from gateway_v2.domain.plan import Phase
from gateway_v2.edge.wire.openai_template import JOINED_CHANNEL_ORDER
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import (
    _MS_PER_S,
    _SCAN_CALIBRATION_MAX_PASS_S,
    _SCAN_CALIBRATION_PASSES,
    _SCAN_CALIBRATION_RETRIES,
    _SCAN_COST_MARGIN,
    _SCAN_SLOT_HEADROOM,
)

__all__ = (
    "CALIBRATION_SLOTS",
    "SCAN_COST_ENV",
    "ScanSizing",
    "export_for_workers",
    "measure_scan_slot_cost_us",
    "scan_slot",
    "scan_sizing",
    "scanned_slots_floor",
)

logger = logging.getLogger(__name__)

#: Worst-case short slots: an injection phrase, a secret-shaped token, a tool name, padding.
CALIBRATION_SLOTS = ("ignore_previous_instr", "AKIA" + "0" * 16, "get_current_weather_x", "a" * 24)

#: Per-slot cost in microseconds (margin included) shared by the parent with its workers.
SCAN_COST_ENV = "AMF_SCAN_SLOT_COST_US"

_US_PER_S = _MS_PER_S * _MS_PER_S


@dataclass(frozen=True, slots=True)
class ScanSizing:
    """How the served `max_scanned_slots` was decided, for the bound and the boot log."""

    cost_us: float
    source: str
    floor: int
    calibrated_slots: int
    served_slots: int

    @property
    def clamped(self) -> bool:
        return self.served_slots > self.calibrated_slots

    def boot_line(self) -> str:
        line = (
            f"boot: tool_bounds -> max_scanned_slots={self.served_slots} (scan cost "
            f"{self.cost_us:.0f} us/slot, {self.source}; floor {self.floor})"
        )
        if self.clamped:
            line += (
                f"; CLAMPED from {self.calibrated_slots}: this host cannot scan a realistic "
                "tool request inside the scan budget, so such scans may overrun it"
            )
        return line


def scanned_slots_floor() -> int:
    """The fewest slots a realistic tool request needs, with the design's headroom."""
    return math.ceil(len(JOINED_CHANNEL_ORDER) / _SCAN_SLOT_HEADROOM)


def scan_slot(text: str, budget_s: float) -> int:
    """Run the GW09 detectors over one slot; returns the finding count (keeps the work live)."""
    context = DetectorContext(phase=Phase.INPUT, rules=(), budget=Budget(budget_s))
    return sum(len(detector().detect(text, context)) for detector in BASE_DETECTORS)


def _measure_once(budget_s: float, pass_s: float) -> float:
    """One calibration: the median of N passes of `pass_s` each, per slot, x margin."""
    scan_slot(CALIBRATION_SLOTS[0], budget_s)  # warm caches and lazy compiles
    per_slot: list[float] = []
    for _ in range(_SCAN_CALIBRATION_PASSES):
        slots = 0
        start = time.perf_counter()
        elapsed = 0.0
        while elapsed < pass_s:
            for text in CALIBRATION_SLOTS:
                scan_slot(text, budget_s)
            slots += len(CALIBRATION_SLOTS)
            elapsed = time.perf_counter() - start
        per_slot.append(elapsed / slots * _US_PER_S)
    return statistics.median(per_slot) * _SCAN_COST_MARGIN


@functools.cache
def measure_scan_slot_cost_us(budget_s: float, window_s: float, floor: int) -> float:
    """This host's per-slot scan cost in microseconds, margin included. Cached per process.

    Refuses (`CapacityUnavailable`) a window that is not finite and positive, and a measured
    cost that is not finite and positive: either would size an unbounded or meaningless bound.
    """
    if not (math.isfinite(window_s) and window_s > 0):
        raise CapacityUnavailable("scan window must be finite and positive to calibrate")
    pass_s = min(window_s, _SCAN_CALIBRATION_MAX_PASS_S)
    window_us = window_s * _US_PER_S
    cost = _measure_once(budget_s, pass_s)
    for _ in range(_SCAN_CALIBRATION_RETRIES):
        if math.floor(window_us / cost) >= floor:
            break
        cost = min(cost, _measure_once(budget_s, pass_s))
    if not (math.isfinite(cost) and cost > 0):
        raise CapacityUnavailable("measured scan slot cost is not finite and positive")
    return cost


def _declared_cost(env: Mapping[str, str]) -> float | None:
    raw = (env.get(SCAN_COST_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise CapacityUnavailable(f"{SCAN_COST_ENV} is not a number") from exc
    if not (math.isfinite(value) and value > 0):
        raise CapacityUnavailable(f"{SCAN_COST_ENV} must be finite and positive")
    return value


def scan_sizing(contract: object, env: Mapping[str, str] | None = None) -> ScanSizing | None:
    """The cost, floor and served bound for `contract`, or `None` for a contract without them.

    Only the real `ResourceContract` has `tool_bounds`, `guard_wait_budget_s` and
    `scan_window_s`; the standalone fakes keep the declared path in `resolve_tool_bounds`.
    """
    resolver = getattr(contract, "tool_bounds", None)
    budget = getattr(contract, "guard_wait_budget_s", None)
    window = getattr(contract, "scan_window_s", None)
    if not (callable(resolver) and callable(budget) and callable(window)):
        return None
    source_env = os.environ if env is None else env
    floor = scanned_slots_floor()
    declared = _declared_cost(source_env)
    if declared is None:
        cost = measure_scan_slot_cost_us(float(budget()), float(window()), floor)
        source = "measured at boot"
    else:
        cost, source = declared, SCAN_COST_ENV
    calibrated = math.floor(float(window()) * _US_PER_S / cost)  # may be 0 on a slow host
    served = resolver(slot_cost_us=cost, scanned_slots_floor=floor).max_scanned_slots
    sizing = ScanSizing(cost, source, floor, calibrated, served)
    if sizing.clamped:
        logger.warning("%s", sizing.boot_line())
    return sizing


def export_for_workers(contract: object, env: MutableMapping[str, str] | None = None) -> None:
    """Share the parent's calibration with every worker it spawns (they inherit the env).

    A value already present (an operator pin, or a re-exec) is kept.
    """
    target = os.environ if env is None else env
    if (target.get(SCAN_COST_ENV) or "").strip():
        return
    sizing = scan_sizing(contract, target)
    if sizing is not None:
        target[SCAN_COST_ENV] = repr(sizing.cost_us)
