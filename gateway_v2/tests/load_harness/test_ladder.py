"""Rate-ladder highest-pass / first-fail property + unit tests (B6 / R2-21), task 7.3.

Covers the pure ``load_harness.ladder`` module only (no I/O, no server, no event loop): the
``evaluate_ladder`` (pre-collected per-rate repeat outcomes) and ``climb_ladder`` (injected
``run_rate`` stub) decision paths, and the pure ``next_rate`` / ``run_passes`` helpers.

* **Property 8 — Ladder highest-pass / first-fail invariants** (task 7.3): for any assignment of
  per-rate pass/fail results (synthesized ``RunOutcome`` tuples so some rates pass 3/3, some fail
  3/3, some are mixed, some generator-bound), ``evaluate_ladder`` returns
  ``highest_repeatable_pass_rate`` == the greatest rate whose step passed 3/3 (Req 10.4);
  ``first_repeatable_fail_rate`` == the lowest rate failing 3/3 that is strictly ABOVE every passing
  rate and NEVER a rate that passed at least once (Req 10.5); generator-bound and mixed steps are
  excluded (neither pass nor fail); and ``next_rate`` / ``climb_ladder`` advance by <= 10% (Req
  10.1). The non-monotone case — a 3/3-fail rate BELOW a 3/3-pass rate is NOT reported as the
  first-fail — is driven directly.
* **Unit tests**: ``run_passes`` single-run classification (C4 bound, infra ceiling, schedule-drop,
  generator-bound exclusion); ``next_rate`` exact 10% step; empty ladder.

House idiom: a seeded ``random.Random`` driven for >= 10,000 iterations with the seed logged in the
assertion message; NO ``hypothesis``. The pass bound is a bare float (the ladder accepts
``float | ResourceContract``) so no live contract is needed; the infra ceiling is read from
``config.default_tolerances().max_infra_error_share``, never hardcoded. Test files are not under the
import-linter layer contract.
"""

from __future__ import annotations

import math
import random

from load_harness.config import LadderConfig, default_ladder_config, default_tolerances
from load_harness.ladder import (
    RunOutcome,
    climb_ladder,
    evaluate_ladder,
    next_rate,
    run_passes,
)

#: Total driving iterations (house idiom >= 10,000).
_ITERATIONS = 10_000

#: The offline SLO pass bound in milliseconds. A bare float (the ladder resolves
#: ``float | ResourceContract``) so the property test needs no live ``ResourceContract``.
_BOUND_MS = 100.0

#: A C4 p99 comfortably BELOW ``_BOUND_MS`` → a passing run (holding infra/drops clean).
_PASS_C4_MS = 50.0

#: A C4 p99 ABOVE ``_BOUND_MS`` → a failing run.
_FAIL_C4_MS = 150.0


def _passing_outcome() -> RunOutcome:
    """A clean passing run: C4 below bound, zero infra error, zero schedule drops."""
    return RunOutcome(
        c4_p99_ms=_PASS_C4_MS,
        infra_error_share=0.0,
        schedule_drops=0,
        generator_bound=False,
    )


def _failing_outcome() -> RunOutcome:
    """A clean failing run: C4 above bound, otherwise clean (a genuine server fail)."""
    return RunOutcome(
        c4_p99_ms=_FAIL_C4_MS,
        infra_error_share=0.0,
        schedule_drops=0,
        generator_bound=False,
    )


def _generator_bound_outcome() -> RunOutcome:
    """A generator-bound run: excluded, neither a pass nor a fail (Req 2.2)."""
    return RunOutcome(
        c4_p99_ms=_PASS_C4_MS,
        infra_error_share=0.0,
        schedule_drops=0,
        generator_bound=True,
    )


# The four per-rate step kinds the property synthesizes. "pass" → 3/3 clean pass; "fail" → 3/3 clean
# fail; "mixed" → one pass + two fail (neither); "generator_bound" → contains a generator-bound
# repeat (excluded).
_STEP_KINDS = ("pass", "fail", "mixed", "generator_bound")


def _outcomes_for_kind(kind: str, config: LadderConfig) -> tuple[RunOutcome, ...]:
    """Synthesize ``config.repeat_count`` ``RunOutcome`` values realizing ``kind``."""
    n = config.repeat_count
    if kind == "pass":
        return tuple(_passing_outcome() for _ in range(n))
    if kind == "fail":
        return tuple(_failing_outcome() for _ in range(n))
    if kind == "generator_bound":
        # One generator-bound repeat is enough to exclude the whole step.
        return (_generator_bound_outcome(), *(_passing_outcome() for _ in range(n - 1)))
    # "mixed": at least one pass and at least one fail → neither 3/3-pass nor 3/3-fail.
    return (_passing_outcome(), *(_failing_outcome() for _ in range(n - 1)))


# --------------------------------------------------------------------------- #
# Task 7.3 — Property 8: ladder highest-pass / first-fail invariants
# Feature: load-harness-r2-21, Property 8: ladder highest-pass first-fail invariants
# Validates: Requirements 10.1, 10.2, 10.4, 10.5
# --------------------------------------------------------------------------- #


def test_property8_evaluate_ladder_highest_pass_first_fail_invariants() -> None:
    """``evaluate_ladder`` obeys the highest-pass / first-fail invariants over random ladders.

    For many random ladders (ascending rates, each step independently pass/fail/mixed/generator-
    bound), assert against an independent reference computation:

    * ``highest_repeatable_pass_rate`` is the greatest rate whose step is a clean 3/3 pass, else
      ``None`` (Req 10.4);
    * ``first_repeatable_fail_rate`` is the lowest 3/3-fail rate strictly ABOVE every passing rate,
      and therefore NEVER a rate that passed at least once (Req 10.5);
    * every mixed or generator-bound step is excluded — it is neither the highest-pass nor the
      first-fail.

    The infra ceiling comes from ``config.default_tolerances()``; the pass bound is a bare float.
    """
    seed = 0x21_73
    rng = random.Random(seed)
    config = default_ladder_config()
    max_infra_error_share = default_tolerances().max_infra_error_share
    for i in range(_ITERATIONS):
        n_steps = rng.randint(0, 8)
        # Ascending, strictly-increasing offered rates (the ladder's natural climb order).
        rate = rng.uniform(1.0, 50.0)
        rate_outcomes: list[tuple[float, tuple[RunOutcome, ...]]] = []
        kinds: list[str] = []
        for _ in range(n_steps):
            kind = rng.choice(_STEP_KINDS)
            kinds.append(kind)
            rate_outcomes.append((rate, _outcomes_for_kind(kind, config)))
            rate = next_rate(rate, config)

        result = evaluate_ladder(
            rate_outcomes,
            _BOUND_MS,
            config,
            max_infra_error_share=max_infra_error_share,
        )

        # Independent reference: the greatest clean-pass rate and the lowest clean-fail rate that
        # sits strictly above every passing rate.
        pass_rates = [r for (r, _), k in zip(rate_outcomes, kinds) if k == "pass"]
        fail_rates = [r for (r, _), k in zip(rate_outcomes, kinds) if k == "fail"]
        expected_highest_pass = max(pass_rates) if pass_rates else None
        expected_first_fail: float | None = None
        for r in sorted(fail_rates):
            if expected_highest_pass is None or r > expected_highest_pass:
                expected_first_fail = r
                break

        ctx = f"seed={seed:#x} iter={i} kinds={kinds}"
        assert result.highest_repeatable_pass_rate == expected_highest_pass, (
            f"{ctx} highest_pass={result.highest_repeatable_pass_rate} "
            f"!= expected={expected_highest_pass}"
        )
        assert result.first_repeatable_fail_rate == expected_first_fail, (
            f"{ctx} first_fail={result.first_repeatable_fail_rate} "
            f"!= expected={expected_first_fail}"
        )

        # Req 10.5 directly: the reported fail rate is never a rate that passed at least once, and
        # is always strictly above the highest pass.
        if result.first_repeatable_fail_rate is not None:
            assert result.first_repeatable_fail_rate not in pass_rates, (
                f"{ctx} first_fail {result.first_repeatable_fail_rate} was also a passing rate"
            )
            if result.highest_repeatable_pass_rate is not None:
                assert (
                    result.first_repeatable_fail_rate
                    > result.highest_repeatable_pass_rate
                ), f"{ctx} first_fail not strictly above highest_pass"

        # Step flags: a step is at most one of passed/failed, and mixed/generator-bound is neither.
        for step, kind in zip(result.steps, kinds):
            assert not (step.passed_3of3 and step.failed_3of3), f"{ctx} step both pass+fail"
            if kind == "pass":
                assert step.passed_3of3 and not step.failed_3of3, f"{ctx} pass step misflagged"
            elif kind == "fail":
                assert step.failed_3of3 and not step.passed_3of3, f"{ctx} fail step misflagged"
            else:  # mixed / generator_bound → excluded from both
                assert not step.passed_3of3 and not step.failed_3of3, (
                    f"{ctx} excluded step ({kind}) was flagged pass/fail"
                )

        # Req 10.1: adjacent rate steps advance by <= 10%.
        for prev_step, curr_step in zip(result.steps, result.steps[1:]):
            ratio = curr_step.offered_rate_per_s / prev_step.offered_rate_per_s
            assert ratio <= 1.0 + config.max_step_fraction + 1e-9, (
                f"{ctx} step advanced by more than {config.max_step_fraction:.0%}: ratio={ratio}"
            )


def test_property8_non_monotone_fail_below_pass_not_reported() -> None:
    """A 3/3-fail rate BELOW a 3/3-pass rate is NEVER the reported first-fail (Req 10.5).

    Builds the explicit non-monotone ladder: fail @ 10, pass @ 11, pass @ 12.1 — the low fail rate
    sits below two passing rates, so ``first_repeatable_fail_rate`` must be ``None`` (there is no
    fail rate strictly above the highest pass), while ``highest_repeatable_pass_rate`` is the top
    pass. Driven across many seeded shuffles of extra excluded (mixed/generator-bound) steps so the
    invariant holds regardless of surrounding noise.
    """
    seed = 0x21_74
    rng = random.Random(seed)
    config = default_ladder_config()
    max_infra_error_share = default_tolerances().max_infra_error_share
    for i in range(_ITERATIONS):
        # Core non-monotone triple: a low fail beneath two higher passes.
        rate_outcomes: list[tuple[float, tuple[RunOutcome, ...]]] = [
            (10.0, _outcomes_for_kind("fail", config)),
            (11.0, _outcomes_for_kind("pass", config)),
            (12.1, _outcomes_for_kind("pass", config)),
        ]
        # Sprinkle excluded steps at random higher rates (must not change the knee).
        for _ in range(rng.randint(0, 3)):
            noise_rate = rng.uniform(13.0, 20.0)
            noise_kind = rng.choice(("mixed", "generator_bound"))
            rate_outcomes.append((noise_rate, _outcomes_for_kind(noise_kind, config)))

        result = evaluate_ladder(
            rate_outcomes,
            _BOUND_MS,
            config,
            max_infra_error_share=max_infra_error_share,
        )
        ctx = f"seed={seed:#x} iter={i}"
        assert result.highest_repeatable_pass_rate == 12.1, (
            f"{ctx} highest_pass={result.highest_repeatable_pass_rate} != 12.1"
        )
        assert result.first_repeatable_fail_rate is None, (
            f"{ctx} non-monotone low fail wrongly reported: "
            f"{result.first_repeatable_fail_rate}"
        )


def test_property8_climb_ladder_matches_invariants_with_injected_run_rate() -> None:
    """``climb_ladder`` (injected ``run_rate`` stub) obeys the same invariants and <= 10% steps.

    An injected pure ``run_rate`` returns a passing ``RunOutcome`` while the rate is below a random
    ``knee_rate`` and a failing one at/above it (a monotone server). The climb must: advance by
    exactly ``next_rate`` (<= 10%) each step, stop on the first clean 3/3 fail, report the greatest
    passing rate as the highest-pass, and report that first failing rate (which is strictly above
    every pass) as the first-fail — never a rate it passed. Covers the ``climb_ladder`` path
    distinct from the pre-collected ``evaluate_ladder`` path above.
    """
    seed = 0x21_75
    rng = random.Random(seed)
    config = default_ladder_config()
    max_infra_error_share = default_tolerances().max_infra_error_share
    for i in range(_ITERATIONS):
        start_rate = rng.uniform(1.0, 20.0)
        knee_rate = start_rate * rng.uniform(1.05, 3.0)
        max_steps = rng.randint(1, 60)

        def run_rate(rate: float, _knee: float = knee_rate) -> RunOutcome:
            return _failing_outcome() if rate >= _knee else _passing_outcome()

        result = climb_ladder(
            start_rate,
            run_rate,
            _BOUND_MS,
            config,
            max_infra_error_share=max_infra_error_share,
            max_steps=max_steps,
        )
        ctx = f"seed={seed:#x} iter={i} start={start_rate:.4g} knee={knee_rate:.4g} max={max_steps}"

        # <= 10% between every pair of visited rates (Req 10.1).
        for prev_step, curr_step in zip(result.steps, result.steps[1:]):
            ratio = curr_step.offered_rate_per_s / prev_step.offered_rate_per_s
            assert math.isclose(ratio, 1.0 + config.max_step_fraction, rel_tol=1e-9), (
                f"{ctx} climb step ratio {ratio} != 1 + {config.max_step_fraction}"
            )

        pass_rates = [s.offered_rate_per_s for s in result.steps if s.passed_3of3]
        fail_rates = [s.offered_rate_per_s for s in result.steps if s.failed_3of3]

        if result.highest_repeatable_pass_rate is not None:
            assert result.highest_repeatable_pass_rate == max(pass_rates), (
                f"{ctx} highest_pass mismatch"
            )
        else:
            assert not pass_rates, f"{ctx} pass steps exist but highest_pass is None"

        if result.first_repeatable_fail_rate is not None:
            # Monotone climb → exactly one clean fail step, which terminated the climb.
            assert result.first_repeatable_fail_rate == min(fail_rates), (
                f"{ctx} first_fail mismatch"
            )
            assert result.first_repeatable_fail_rate not in pass_rates, (
                f"{ctx} first_fail {result.first_repeatable_fail_rate} was also a pass"
            )
            if result.highest_repeatable_pass_rate is not None:
                assert (
                    result.first_repeatable_fail_rate
                    > result.highest_repeatable_pass_rate
                ), f"{ctx} first_fail not strictly above highest_pass"


# --------------------------------------------------------------------------- #
# Task 7.3 — unit tests (single-run classification + step policy sanity)
# Feature: load-harness-r2-21
# Validates: Requirements 10.1, 10.2, 10.3
# --------------------------------------------------------------------------- #


def test_run_passes_single_run_classification() -> None:
    """``run_passes`` passes only on C4 < bound AND infra <= ceiling AND zero drops (Req 10.3)."""
    ceiling = default_tolerances().max_infra_error_share
    assert run_passes(_passing_outcome(), _BOUND_MS, max_infra_error_share=ceiling)
    # C4 at/above the bound fails (strict <).
    assert not run_passes(
        RunOutcome(
            c4_p99_ms=_BOUND_MS,
            infra_error_share=0.0,
            schedule_drops=0,
            generator_bound=False,
        ),
        _BOUND_MS,
        max_infra_error_share=ceiling,
    )
    # Infra error above the ceiling fails.
    assert not run_passes(
        RunOutcome(
            c4_p99_ms=_PASS_C4_MS,
            infra_error_share=ceiling + 0.01,
            schedule_drops=0,
            generator_bound=False,
        ),
        _BOUND_MS,
        max_infra_error_share=ceiling,
    )
    # A non-zero Schedule_Drop count fails (Req 10.3).
    assert not run_passes(
        RunOutcome(
            c4_p99_ms=_PASS_C4_MS,
            infra_error_share=0.0,
            schedule_drops=1,
            generator_bound=False,
        ),
        _BOUND_MS,
        max_infra_error_share=ceiling,
    )
    # A generator-bound run never passes (excluded, Req 2.2).
    assert not run_passes(_generator_bound_outcome(), _BOUND_MS, max_infra_error_share=ceiling)


def test_next_rate_advances_by_exactly_the_step_fraction() -> None:
    """``next_rate`` advances by exactly ``max_step_fraction`` (<= 10%) (Req 10.1)."""
    config = default_ladder_config()
    assert config.max_step_fraction <= 0.10
    assert next_rate(100.0, config) == 100.0 * (1.0 + config.max_step_fraction)


def test_empty_ladder_reports_no_knee() -> None:
    """An empty ladder yields ``None`` knees and no steps (edge case)."""
    config = default_ladder_config()
    result = evaluate_ladder(
        [],
        _BOUND_MS,
        config,
        max_infra_error_share=default_tolerances().max_infra_error_share,
    )
    assert result.highest_repeatable_pass_rate is None
    assert result.first_repeatable_fail_rate is None
    assert result.steps == ()
