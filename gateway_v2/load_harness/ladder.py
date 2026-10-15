"""Rate ladder: the pure highest-pass / first-fail decision engine (B6 / R2-21).

The ladder is the measurement procedure's **decision engine** (Req 10). It climbs offered rate in
small steps (≤ ``LadderConfig.max_step_fraction`` ≤ 0.10, Req 10.1), runs ``repeat_count`` repeats
(3, Req 10.2) at each candidate rate, classifies each single run as a pass or fail against the SLO
bound (Req 10.3), and reports the ``Highest_Repeatable_Pass`` (the greatest rate passing 3/3, Req
10.4) and the ``First_Repeatable_Fail`` (the lowest rate failing 3/3, and never a rate that passed
even once, Req 10.5).

PURE + INJECTED (so it is property-testable offline). The ladder does **not** run the driver or
touch a server: the caller supplies, per candidate rate, the ``repeat_count`` ``RunOutcome`` values
(or a callable that produces one ``RunOutcome`` for a given rate), and the ladder computes the
pass/fail classification and the two knee rates. This keeps the climb policy a referentially
transparent function of ``(rates, outcomes, pass_bound, config)`` — the archetype the Property 8
test (``test_ladder.py``, task 7.3) and the unit edge cases (task 7.4) exercise.

Pass bound sourced from the capacity authority (Req 10.3, 14.2). The SLO pass bound is **not** a
literal here and **not** a field on ``LadderConfig``. ``evaluate_ladder`` / ``climb_ladder`` accept
a ``PassBound = float | ResourceContract``; a ``ResourceContract`` contributes its
``target_p99_ms`` (the one capacity authority), a bare float is used directly (for offline property
tests). The module imports ``ResourceContract`` only for the type — it never loads a live contract
at module scope, and holds no capacity-position literal (the ``check_capacity_literals`` gate).

Generator-bound runs are EXCLUDED (Req 2.2). A ``RunOutcome`` flagged ``generator_bound`` is neither
a pass nor a fail — it means the *load generator*, not the server, was the bottleneck (headroom
below ``_MIN_HEADROOM_MARGIN``, measured in ``driver.py``), so it must not be read as a server
pass/fail. A rate whose repeats are not a clean 3/3 pass and not a clean 3/3 fail (e.g. mixed, or
any generator-bound repeat) is simply neither the highest-pass nor the first-fail.

No module-level mutable; both value types are frozen + slots (Req 14.2, 14.3). ``LadderStep`` and
``LadderResult`` are immutable, byte-reproducible values.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from gateway_v2.runtime.resources import ResourceContract
from load_harness.config import LadderConfig

__all__ = (
    "LadderResult",
    "LadderStep",
    "PassBound",
    "RunOutcome",
    "climb_ladder",
    "evaluate_ladder",
    "next_rate",
    "run_passes",
)

#: The pass bound (the SLO the C4 p99 of a run is judged against). Either the one capacity authority
#: ``ResourceContract`` (whose ``target_p99_ms`` is read) or a bare ``float`` in milliseconds (for
#: offline property/unit tests). Never a module-level literal here (Req 10.3, 14.2).
PassBound = float | ResourceContract


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """The outcome of ONE offered run at a candidate rate, as fed to the ladder (Req 10.3).

    ``c4_p99_ms`` is the run's client-side C4 p99 (``+inf`` for an all-errored run, from ``c4.py``);
    ``infra_error_share`` is the share of offered requests that failed with a *harness/transport*
    (infra) error, in ``[0.0, 1.0]``; ``schedule_drops`` is the count of ``Schedule_Drop`` events
    (dispatches the *generator* fell behind on, from ``driver.py``); ``generator_bound`` is True
    when the run's Loadgen_Headroom fell below ``_MIN_HEADROOM_MARGIN`` — such a run is EXCLUDED,
    neither a pass nor a fail (Req 2.2). The ladder reads these only; it never runs the driver.
    """

    c4_p99_ms: float
    infra_error_share: float
    schedule_drops: int
    generator_bound: bool


@dataclass(frozen=True, slots=True)
class LadderStep:
    """One rate step of the ladder: the ``offered_rate_per_s`` and its ``repeat_count`` outcomes.

    ``outcomes`` is the ordered tuple of ``RunOutcome`` values for the repeats run at this rate
    (length ``LadderConfig.repeat_count`` on a complete step, Req 10.2). ``passed_3of3`` is True iff
    every outcome passed the single-run pass test (Req 10.3) and no outcome was generator-bound;
    ``failed_3of3`` is True iff every outcome was a clean (non-generator-bound) fail; a step is at
    most one of the two (a mixed or generator-bound-containing step is neither). Frozen + slots.
    """

    offered_rate_per_s: float
    outcomes: tuple[RunOutcome, ...]
    passed_3of3: bool
    failed_3of3: bool


@dataclass(frozen=True, slots=True)
class LadderResult:
    """The ladder's knee decision over all evaluated rate steps (Req 10.4, 10.5).

    ``highest_repeatable_pass_rate`` is the greatest offered rate whose step passed 3/3 (``None``
    if no step did); ``first_repeatable_fail_rate`` is the lowest offered rate whose step failed
    3/3 and that is strictly above every passing rate (``None`` if no such step) — so the reported
    failing rate is NEVER a rate that passed at least once (Req 10.5), enforced by construction.
    ``steps`` is the ordered tuple of every evaluated ``LadderStep``. Frozen + slots.
    """

    highest_repeatable_pass_rate: float | None
    first_repeatable_fail_rate: float | None
    steps: tuple[LadderStep, ...]


def _resolve_pass_bound(pass_bound: PassBound) -> float:
    """Resolve ``PassBound`` to the SLO bound in milliseconds.

    A ``ResourceContract`` contributes its ``target_p99_ms`` (the one capacity authority, Req 10.3,
    14.2); a bare ``float`` is used directly. No literal is introduced here.
    """
    if isinstance(pass_bound, ResourceContract):
        return pass_bound.target_p99_ms
    return pass_bound


def run_passes(
    outcome: RunOutcome,
    pass_bound: PassBound,
    *,
    max_infra_error_share: float,
) -> bool:
    """Classify a SINGLE run as passing (Req 10.3).

    A run passes iff all three hold: its C4 p99 is strictly below the SLO bound (resolved from
    ``pass_bound`` — a ``ResourceContract``'s ``target_p99_ms`` or a bare float), its infra-error
    share is within ``max_infra_error_share`` (the config's ``Tolerances.max_infra_error_share``,
    which defaults to ``_MAX_INFRA_ERROR_SHARE``), and its ``Schedule_Drop`` count is zero. A
    ``generator_bound`` run is EXCLUDED and therefore never passes (Req 2.2) — ``_classify_step``
    likewise excludes it from failing. The infra ceiling is injected, never a literal here.
    """
    return _run_passes(
        outcome,
        bound_ms=_resolve_pass_bound(pass_bound),
        max_infra_error_share=max_infra_error_share,
    )


def _run_passes(outcome: RunOutcome, *, bound_ms: float, max_infra_error_share: float) -> bool:
    if outcome.generator_bound:
        return False
    return (
        outcome.c4_p99_ms < bound_ms
        and outcome.infra_error_share <= max_infra_error_share
        and outcome.schedule_drops == 0
    )


def next_rate(current_rate_per_s: float, config: LadderConfig) -> float:
    """The next ladder rate above ``current_rate_per_s`` (Req 10.1).

    Advances by exactly ``config.max_step_fraction`` (≤ 0.10) so the step is at most 10% — the
    finest-grained ascending step the policy admits, keeping the resolved knee repeatable. A pure
    function of the current rate and the climb policy; no capacity literal.
    """
    return current_rate_per_s * (1.0 + config.max_step_fraction)


def _classify_step(
    *,
    offered_rate_per_s: float,
    outcomes: Sequence[RunOutcome],
    bound_ms: float,
    config: LadderConfig,
    max_infra_error_share: float,
) -> LadderStep:
    """Build one ``LadderStep`` from a candidate rate's repeat outcomes (Req 10.2, 10.3).

    A step is a clean 3/3 PASS iff it has exactly ``config.repeat_count`` outcomes, none is
    generator-bound, and every one passes the single-run test; a clean 3/3 FAIL iff it has exactly
    ``config.repeat_count`` outcomes, none is generator-bound, and every one fails. A step with a
    generator-bound repeat, a mixed result, or the wrong number of repeats is neither (excluded from
    both knee rates, Req 2.2).
    """
    frozen = tuple(outcomes)
    complete = len(frozen) == config.repeat_count
    any_generator_bound = any(o.generator_bound for o in frozen)
    passes = tuple(
        _run_passes(o, bound_ms=bound_ms, max_infra_error_share=max_infra_error_share)
        for o in frozen
    )
    passed_3of3 = complete and not any_generator_bound and all(passes)
    failed_3of3 = complete and not any_generator_bound and not any(passes)
    return LadderStep(
        offered_rate_per_s=offered_rate_per_s,
        outcomes=frozen,
        passed_3of3=passed_3of3,
        failed_3of3=failed_3of3,
    )


def _knee(steps: Sequence[LadderStep]) -> tuple[float | None, float | None]:
    """Compute (highest_repeatable_pass, first_repeatable_fail) from classified steps.

    ``Highest_Repeatable_Pass`` is the greatest rate of any 3/3-passing step (Req 10.4).
    ``First_Repeatable_Fail`` is the lowest rate of any 3/3-failing step that is strictly ABOVE
    every passing rate — enforcing "never a rate that passed at least once" by construction (Req
    10.5). A failing step at or below the highest pass is not eligible to be the reported
    first-fail (a non-monotone ladder cannot report a failing rate the server already passed).
    """
    pass_rates = sorted(s.offered_rate_per_s for s in steps if s.passed_3of3)
    highest_pass = pass_rates[-1] if pass_rates else None
    fail_rates = sorted(s.offered_rate_per_s for s in steps if s.failed_3of3)
    first_fail: float | None = None
    for rate in fail_rates:
        if highest_pass is None or rate > highest_pass:
            first_fail = rate
            break
    return highest_pass, first_fail


def evaluate_ladder(
    rate_outcomes: Sequence[tuple[float, Sequence[RunOutcome]]],
    pass_bound: PassBound,
    config: LadderConfig,
    *,
    max_infra_error_share: float,
) -> LadderResult:
    """Decide the knee from pre-collected per-rate repeat outcomes (Req 10.3, 10.4, 10.5).

    ``rate_outcomes`` is the ordered sequence of ``(offered_rate_per_s, outcomes)`` pairs — each
    ``outcomes`` the ``config.repeat_count`` ``RunOutcome`` values the caller
    (``driver``/``dryrun``) collected at that rate. The pass bound is resolved from ``pass_bound``
    (a ``ResourceContract``'s ``target_p99_ms`` or a bare float). The result's
    ``highest_repeatable_pass_rate`` /
    ``first_repeatable_fail_rate`` obey the Req 10.4 / 10.5 invariants by construction (see
    ``_knee``). A PURE function — it runs no driver.
    """
    bound_ms = _resolve_pass_bound(pass_bound)
    steps = tuple(
        _classify_step(
            offered_rate_per_s=rate,
            outcomes=outcomes,
            bound_ms=bound_ms,
            config=config,
            max_infra_error_share=max_infra_error_share,
        )
        for rate, outcomes in rate_outcomes
    )
    highest_pass, first_fail = _knee(steps)
    return LadderResult(
        highest_repeatable_pass_rate=highest_pass,
        first_repeatable_fail_rate=first_fail,
        steps=steps,
    )


def climb_ladder(
    start_rate_per_s: float,
    run_rate: Callable[[float], RunOutcome],
    pass_bound: PassBound,
    config: LadderConfig,
    *,
    max_infra_error_share: float,
    max_steps: int,
) -> LadderResult:
    """Climb the ladder from ``start_rate_per_s``, collecting repeats via an injected ``run_rate``.

    ``run_rate`` is the injected per-run function the caller supplies — in the dry-run it drives the
    booted gateway and returns the measured ``RunOutcome``; in a property/unit test it is a pure
    stub. The climb runs ``config.repeat_count`` repeats at each rate, advances by ``next_rate``
    (≤ 10% steps, Req 10.1), and stops after a 3/3 fail step or ``max_steps`` rates — then delegates
    the knee decision to ``evaluate_ladder`` so the climb and the pre-collected path share ONE
    classification (Req 10.4, 10.5). The ladder owns no I/O; all effects live in ``run_rate``.
    """
    bound_ms = _resolve_pass_bound(pass_bound)
    collected: list[tuple[float, Sequence[RunOutcome]]] = []
    rate = start_rate_per_s
    for _ in range(max(max_steps, 0)):
        outcomes = tuple(run_rate(rate) for _ in range(config.repeat_count))
        collected.append((rate, outcomes))
        step = _classify_step(
            offered_rate_per_s=rate,
            outcomes=outcomes,
            bound_ms=bound_ms,
            config=config,
            max_infra_error_share=max_infra_error_share,
        )
        if step.failed_3of3:
            break
        rate = next_rate(rate, config)
    return evaluate_ladder(
        collected,
        pass_bound,
        config,
        max_infra_error_share=max_infra_error_share,
    )
