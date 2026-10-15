"""Open-loop arrival generator property + unit tests (B6 / R2-21), task 3.2.

Covers the pure ``load_harness.arrivals`` module only (no I/O, no server, no event loop):

* **Property 1 — Poisson arrivals match the configured process within tolerance** (task 3.2): for
  any seed and offered rate, the realized MEAN inter-arrival time of a Poisson ``ArrivalSchedule``
  is within the declared relative tolerance of ``1 / offered_rate`` (the exponential mean) over a
  large sample. The tolerance comes from ``config`` (``Tolerances.arrival_mean_relative`` /
  ``default_tolerances()``), never hardcoded here.
* **Unit tests**: a CONSTANT-mode schedule produces exactly ``1 / rate`` gaps (sanity); a non-
  positive ``count`` yields an empty schedule; indices are ``0..count-1`` ascending and
  ``scheduled_s`` is monotonically increasing.

House idiom: seeded ``random.Random`` loops of >= 10,000 iterations with the seed logged in the
assertion message; NO ``hypothesis``. The clock is injected (a fixed ``lambda``) so the machine is
deterministic. Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import random

from load_harness.arrivals import ArrivalSchedule, build_schedule
from load_harness.config import ArrivalMode, default_tolerances

#: Total driving iterations across all seed/rate draws (house idiom >= 10,000).
_ITERATIONS = 10_000

#: Per-schedule arrival sample. Large enough that the sample mean of the exponential inter-arrivals
#: converges to ``1 / rate`` within the declared relative tolerance for every rate drawn below. The
#: standard error of the mean of ``n`` exponential samples is ``mean / sqrt(n)``; at ``n = 20,000``
#: that is ~0.7% of the mean, comfortably inside the 5% ``arrival_mean_relative`` tolerance with
#: margin for the random spread across 10,000 seeds.
_SAMPLE_COUNT = 20_000

#: A fixed injected clock start reference; the schedule's absolute offset is irrelevant to the
#: inter-arrival statistic (gaps are differences), so any constant is fine and keeps the test
#: deterministic without touching the wall clock.
_CLOCK_START_S = 1_000.0


def _mean_inter_arrival_s(schedule: ArrivalSchedule, *, start_s: float) -> float:
    """Mean of the realized inter-arrival gaps of ``schedule`` (first gap from ``start_s``).

    ``build_schedule`` accumulates gaps onto the clock's ``start_s``; the gap preceding arrival
    ``k`` is ``arrivals[k].scheduled_s - prev`` with ``prev`` the previous arrival's ``scheduled_s``
    (or ``start_s`` for the first). The mean over all ``count`` gaps is the realized exp. mean.
    """
    prev = start_s
    total = 0.0
    for arrival in schedule.arrivals:
        total += arrival.scheduled_s - prev
        prev = arrival.scheduled_s
    return total / len(schedule.arrivals)


# --------------------------------------------------------------------------- #
# Task 3.2 — Property 1: Poisson arrivals match the configured process within tolerance
# Feature: load-harness-r2-21, Property 1: Poisson arrivals match the configured process within
# tolerance
# Validates: Requirements 1.2, 12.3
# --------------------------------------------------------------------------- #


def test_property1_poisson_mean_inter_arrival_within_tolerance() -> None:
    """Realized Poisson mean inter-arrival is within ``arrival_mean_relative`` of ``1 / rate``.

    For many seeds and offered rates, build a Poisson ``ArrivalSchedule`` over a large sample and
    assert the realized mean inter-arrival time matches the exponential mean ``1 / offered_rate``
    within the declared RELATIVE tolerance read from ``config.default_tolerances()`` — never a
    hardcoded tolerance (Req 1.2, 12.3).
    """
    seed = 0x21_01
    rng = random.Random(seed)
    tolerance = default_tolerances().arrival_mean_relative
    iterations = _ITERATIONS // 20  # each iteration draws _SAMPLE_COUNT arrivals
    for i in range(iterations):
        offered_rate_per_s = rng.uniform(0.5, 500.0)
        expected_mean_s = 1.0 / offered_rate_per_s
        schedule = build_schedule(
            mode=ArrivalMode.POISSON,
            offered_rate_per_s=offered_rate_per_s,
            count=_SAMPLE_COUNT,
            rng=rng,
            clock=lambda: _CLOCK_START_S,
        )
        realized_mean_s = _mean_inter_arrival_s(schedule, start_s=_CLOCK_START_S)
        relative_error = abs(realized_mean_s - expected_mean_s) / expected_mean_s
        assert relative_error <= tolerance, (
            f"seed={seed:#x} iter={i} rate={offered_rate_per_s:.4f} "
            f"realized_mean={realized_mean_s:.6g} expected_mean={expected_mean_s:.6g} "
            f"relative_error={relative_error:.6f} > tolerance={tolerance}"
        )


def test_property1_total_iterations_cover_the_input_space() -> None:
    """The Property 1 sample sweep drives >= 10,000 total arrival draws (house idiom).

    ``iterations`` schedules each of ``_SAMPLE_COUNT`` arrivals is far above the 10,000-iteration
    floor; this guards the constants against being lowered below the idiom.
    """
    iterations = _ITERATIONS // 20
    assert iterations * _SAMPLE_COUNT >= 10_000, (
        f"total arrival draws {iterations * _SAMPLE_COUNT} < 10,000 house-idiom floor"
    )


# --------------------------------------------------------------------------- #
# Task 3 — unit tests (sanity / edge cases)
# Feature: load-harness-r2-21
# Validates: Requirements 1.6, 1.2, 13.3
# --------------------------------------------------------------------------- #


def test_constant_mode_produces_exact_reciprocal_gaps() -> None:
    """CONSTANT mode yields exactly ``1 / rate`` inter-arrival gaps, consuming no randomness (1.6).

    Every gap must equal ``1 / offered_rate`` to floating-point exactness; the schedule is a pure
    arithmetic accumulation independent of the injected ``rng``.
    """
    offered_rate_per_s = 8.0
    gap = 1.0 / offered_rate_per_s
    count = 100
    schedule = build_schedule(
        mode=ArrivalMode.CONSTANT,
        offered_rate_per_s=offered_rate_per_s,
        count=count,
        rng=random.Random(0),
        clock=lambda: _CLOCK_START_S,
    )
    assert len(schedule.arrivals) == count
    prev = _CLOCK_START_S
    for k, arrival in enumerate(schedule.arrivals):
        assert arrival.scheduled_s == _CLOCK_START_S + gap * (k + 1), (
            f"constant gap mismatch at index {k}: scheduled_s={arrival.scheduled_s}"
        )
        prev = arrival.scheduled_s
    assert prev == _CLOCK_START_S + gap * count


def test_non_positive_count_yields_empty_schedule() -> None:
    """A zero or negative ``count`` produces an empty schedule for both modes (edge case)."""
    for mode in (ArrivalMode.POISSON, ArrivalMode.CONSTANT):
        for count in (0, -1, -100):
            schedule = build_schedule(
                mode=mode,
                offered_rate_per_s=10.0,
                count=count,
                rng=random.Random(1),
                clock=lambda: _CLOCK_START_S,
            )
            assert schedule.arrivals == ()
            assert schedule.mode is mode
            assert schedule.offered_rate_per_s == 10.0


def test_schedule_indices_and_times_are_monotonic() -> None:
    """Indices are ``0..count-1`` and ``scheduled_s`` is strictly increasing from ``start_s``."""
    count = 500
    schedule = build_schedule(
        mode=ArrivalMode.POISSON,
        offered_rate_per_s=50.0,
        count=count,
        rng=random.Random(1234),
        clock=lambda: _CLOCK_START_S,
    )
    assert [a.index for a in schedule.arrivals] == list(range(count))
    prev = _CLOCK_START_S
    for arrival in schedule.arrivals:
        assert arrival.scheduled_s > prev, "Poisson inter-arrival gaps are positive"
        prev = arrival.scheduled_s
