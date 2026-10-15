"""Report + ladder edge-case unit tests (B6 / R2-21), task 7.4.

Representative EXAMPLES (NOT property-based tests — no >= 10,000-iteration loop) exercising the
error conditions and boundary shapes of ``load_harness.report`` and ``load_harness.ladder`` that
the Property 8 test (``test_ladder.py``, task 7.3) does not reach:

* ``GpuBusy`` is a two-arm sum type — ``UNAVAILABLE`` carries no value, ``available_reading``
  carries the float, and the ``__post_init__`` invariant rejects the mixed shapes (Req 11.6, 15.2).
* a ``RunReport`` built under constant arrivals carries ``arrival_mode_constant`` so its limits are
  labeled upper bounds (Req 1.6).
* an all-errored run reports ``c4_p99_ms == +inf`` and does not pass a finite SLO bound (Req 8.5).
* a run at the ladder floor whose headroom is below the margin is generator-bound and EXCLUDED from
  knee determination — neither the highest-pass nor the first-fail (Req 2.2).
* a non-zero ``Schedule_Drop`` count fails the single-run pass test (Req 10.3, 1.4).
* ``ReasonMix.from_counts`` canonicalizes to a sorted, unique, non-negative tuple whose ``total`` is
  the sum of the counts (Req 11.4).

These are plain-``assert`` examples; the file imports nothing beyond the two modules under test and
the config value types they consume. Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import math

import pytest

from load_harness.config import LadderConfig
from load_harness.ladder import RunOutcome, evaluate_ladder, run_passes
from load_harness.report import GpuBusy, ReasonMix, RunReport

# A fixed finite SLO bound (ms) and infra ceiling for the ladder examples. A bare float is an
# accepted ``PassBound`` for offline tests (the live path reads ``ResourceContract.target_p99_ms``).
_BOUND_MS = 20.0
_MAX_INFRA_ERROR_SHARE = 0.001
_LADDER = LadderConfig(max_step_fraction=0.10, repeat_count=3)


def _reason_mix() -> ReasonMix:
    return ReasonMix.from_counts({"admitted": 7, "overload_shed": 3})


# --------------------------------------------------------------------------------------------------
# GpuBusy — the sum type that makes a fabricated GPU-busy number unrepresentable (Req 11.6, 15.2).
# --------------------------------------------------------------------------------------------------


def test_gpu_busy_unavailable_sentinel_has_no_value() -> None:
    """With no source configured the shared sentinel is unavailable and carries no number."""
    assert GpuBusy.UNAVAILABLE.available is False
    assert GpuBusy.UNAVAILABLE.value is None


def test_gpu_busy_available_reading_carries_the_float() -> None:
    """An available reading built from a configured source carries its float value."""
    reading = GpuBusy.available_reading(0.42)
    assert reading.available is True
    assert reading.value == 0.42


def test_gpu_busy_available_with_none_value_is_rejected() -> None:
    """The ``__post_init__`` invariant rejects an available reading with no value."""
    with pytest.raises(ValueError, match="must carry a numeric value"):
        GpuBusy(available=True, value=None)


def test_gpu_busy_unavailable_with_value_is_rejected() -> None:
    """The ``__post_init__`` invariant rejects an unavailable reading that carries a value."""
    with pytest.raises(ValueError, match="must not carry a value"):
        GpuBusy(available=False, value=0.0)


# --------------------------------------------------------------------------------------------------
# ReasonMix — canonical sorted/unique/non-negative frozen distribution (Req 11.4).
# --------------------------------------------------------------------------------------------------


def test_reason_mix_from_counts_is_sorted_unique_with_total() -> None:
    """``from_counts`` canonicalizes to a sorted tuple whose ``total`` equals the sum of counts."""
    mix = ReasonMix.from_counts({"overload_shed": 3, "admitted": 7, "budget_unavailable": 1})
    reasons = [reason for reason, _ in mix.counts]
    assert reasons == sorted(reasons)
    assert len(set(reasons)) == len(reasons)
    assert all(count >= 0 for _, count in mix.counts)
    assert mix.total == 11
    assert mix.as_mapping()["admitted"] == 7


def test_reason_mix_rejects_negative_count() -> None:
    """A negative count is not a well-formed distribution and is rejected at construction."""
    with pytest.raises(ValueError, match="non-negative"):
        ReasonMix(counts=(("admitted", -1),))


# --------------------------------------------------------------------------------------------------
# RunReport — constant-arrival upper-bound labeling (Req 1.6) + all-errored run (Req 8.5).
# --------------------------------------------------------------------------------------------------


def _run_report(*, c4_p99_ms: float, c4_passes: bool, arrival_mode_constant: bool) -> RunReport:
    return RunReport(
        c4_p99_ms=c4_p99_ms,
        slo_bound_ms=_BOUND_MS,
        c4_passes=c4_passes,
        owner_queue_wait_s=0.0,
        event_loop_lag_s=0.0,
        reason_mix=_reason_mix(),
        schedule_drop_count=0,
        loadgen_headroom=0.5,
        generator_bound=False,
        realized_strata_shares=(("long_prompt", 0.05),),
        distinct_keys=2_000,
        distinct_orgs=200,
        distinct_tenants=10_000,
        gpu_busy=GpuBusy.UNAVAILABLE,
        arrival_mode_constant=arrival_mode_constant,
    )


def test_run_report_constant_arrival_flag_round_trips() -> None:
    """A constant-arrival run carries the flag so its published limits are labeled upper bounds."""
    report = _run_report(c4_p99_ms=5.0, c4_passes=True, arrival_mode_constant=True)
    assert report.arrival_mode_constant is True
    poisson = _run_report(c4_p99_ms=5.0, c4_passes=True, arrival_mode_constant=False)
    assert poisson.arrival_mode_constant is False


def test_run_report_all_errored_run_is_infinite_and_fails_finite_bound() -> None:
    """An all-errored run reports ``c4_p99_ms == +inf`` and does not pass a finite SLO bound."""
    report = _run_report(c4_p99_ms=math.inf, c4_passes=False, arrival_mode_constant=False)
    assert report.c4_p99_ms == math.inf
    assert math.isinf(report.c4_p99_ms)
    assert report.c4_passes is False
    assert not (report.c4_p99_ms < report.slo_bound_ms)
    # the sentinel GPU-busy field is never a fabricated number on a local run
    assert report.gpu_busy is GpuBusy.UNAVAILABLE


# --------------------------------------------------------------------------------------------------
# Ladder single-run pass test — Schedule_Drop fails the run (Req 10.3, 1.4).
# --------------------------------------------------------------------------------------------------


def test_run_with_schedule_drop_fails_pass_test() -> None:
    """A non-zero ``Schedule_Drop`` count fails the pass test even when C4 and infra are clean."""
    dropped = RunOutcome(
        c4_p99_ms=1.0,  # well under the bound
        infra_error_share=0.0,
        schedule_drops=1,  # the generator fell behind
        generator_bound=False,
    )
    assert run_passes(dropped, _BOUND_MS, max_infra_error_share=_MAX_INFRA_ERROR_SHARE) is False
    clean = RunOutcome(
        c4_p99_ms=1.0,
        infra_error_share=0.0,
        schedule_drops=0,
        generator_bound=False,
    )
    assert run_passes(clean, _BOUND_MS, max_infra_error_share=_MAX_INFRA_ERROR_SHARE) is True


# --------------------------------------------------------------------------------------------------
# Ladder knee — a headroom-below-margin (generator-bound) run at the floor is EXCLUDED (Req 2.2).
# --------------------------------------------------------------------------------------------------


def test_generator_bound_floor_run_is_excluded_from_knee() -> None:
    """A generator-bound run at the ladder floor is neither the highest-pass nor the first-fail."""
    floor_rate = 100.0
    next_rate = floor_rate * (1.0 + _LADDER.max_step_fraction)
    # Floor rate: all three repeats are generator-bound (headroom below the margin) -> excluded.
    generator_bound_repeats = tuple(
        RunOutcome(c4_p99_ms=1.0, infra_error_share=0.0, schedule_drops=0, generator_bound=True)
        for _ in range(_LADDER.repeat_count)
    )
    # Next rate up: a clean 3/3 fail (C4 over the bound) -> this is the only knee signal.
    failing_repeats = tuple(
        RunOutcome(
            c4_p99_ms=_BOUND_MS + 5.0,
            infra_error_share=0.0,
            schedule_drops=0,
            generator_bound=False,
        )
        for _ in range(_LADDER.repeat_count)
    )
    result = evaluate_ladder(
        ((floor_rate, generator_bound_repeats), (next_rate, failing_repeats)),
        _BOUND_MS,
        _LADDER,
        max_infra_error_share=_MAX_INFRA_ERROR_SHARE,
    )
    # No rate passed 3/3 (the floor run was excluded, not a pass).
    assert result.highest_repeatable_pass_rate is None
    # The excluded floor rate is NOT the first-fail; the clean 3/3-fail rate above it is.
    assert result.first_repeatable_fail_rate == next_rate
    floor_step = next(s for s in result.steps if s.offered_rate_per_s == floor_rate)
    assert floor_step.passed_3of3 is False
    assert floor_step.failed_3of3 is False
