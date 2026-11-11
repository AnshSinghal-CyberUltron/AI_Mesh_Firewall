"""Measure this host's per-slot scan cost at boot, so tool bounds follow the hardware (GW34).

`max_scanned_slots` is "how many detector calls fit in the scan share of the p99 target". A
constant per-call cost measured on one machine is wrong on every other: on GitHub's runners the
declared 200 us was ~280 us, so the derived bound overran its budget by 40%. Calibrating once
per process makes the bound hold on any host and grow with faster CPUs.

The detectors timed are the GW09 deterministic set every scan seam runs (PII, secret, injection
signal), over the worst-case slot shapes the GW34 calibration test uses.
"""
from __future__ import annotations

import functools
import statistics
import time

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.deterministic.detectors import (
    InjectionSignalDetector,
    PiiDetector,
    SecretDetector,
)
from gateway_v2.detect.guard.backend import Budget
from gateway_v2.domain.plan import Phase
from gateway_v2.runtime.resources import (
    _MS_PER_S,
    _SCAN_CALIBRATION_PASSES,
    _SCAN_COST_MARGIN,
    _SCAN_SLOT_COST_US,
)

__all__ = ("CALIBRATION_SLOTS", "measure_scan_slot_cost_us", "scan_slot")

#: Worst-case short slots: an injection phrase, a secret-shaped token, a tool name, padding.
CALIBRATION_SLOTS = ("ignore_previous_instr", "AKIA" + "0" * 16, "get_current_weather_x", "a" * 24)


def scan_slot(text: str, budget_s: float) -> int:
    """Run the GW09 detectors over one slot; returns the finding count (keeps the work live)."""
    context = DetectorContext(phase=Phase.INPUT, rules=(), budget=Budget(budget_s))
    found = 0
    for detector in (PiiDetector(), SecretDetector(), InjectionSignalDetector()):
        found += len(detector.detect(text, context))
    return found


@functools.cache
def measure_scan_slot_cost_us(budget_s: float, window_s: float) -> float:
    """This host's per-slot scan cost in microseconds, already multiplied by the margin.

    Each timed pass scans the worst-case slots round after round until it has lasted
    `window_s` (the scan budget the bound limits), and the median pass is kept. A pass of only
    a few slots fits in one scheduler slice and, on a busy host, understated a full window by
    2-4x; the median of full windows is what a request's scan actually experiences.

    Cached per process: the hardware does not change under a running worker, and every
    `build_app` would otherwise re-measure. A non-positive measurement (clock too coarse) falls
    back to the declared cost rather than deriving an unbounded slot count.
    """
    us_per_s = _MS_PER_S * _MS_PER_S
    scan_slot(CALIBRATION_SLOTS[0], budget_s)  # warm caches and lazy compiles
    per_slot: list[float] = []
    for _ in range(_SCAN_CALIBRATION_PASSES):
        slots = 0
        start = time.perf_counter()
        elapsed = 0.0
        while elapsed < window_s:
            for text in CALIBRATION_SLOTS:
                scan_slot(text, budget_s)
            slots += len(CALIBRATION_SLOTS)
            elapsed = time.perf_counter() - start
        per_slot.append(elapsed / slots * us_per_s)
    cost_us = statistics.median(per_slot)
    if cost_us <= 0:
        return float(_SCAN_SLOT_COST_US)
    return cost_us * _SCAN_COST_MARGIN
