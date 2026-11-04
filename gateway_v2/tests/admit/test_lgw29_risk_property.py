# Feature: gw29-killswitch-isolation, Property 7: risk score bounds, floor, monotone, probation
# Validates: Requirements 5.1, 5.2, 5.6 (score bounds, sample floor, hysteresis)
"""P7 over random outcome sequences, plus the pinned 429 / 5xx cases."""

from __future__ import annotations

import random

from gateway_v2.domain.incident import ModelOutcome as O
from tests.admit._lgw29_support import Clock, contract, observe_many, risk_tracker

SEED = 29007
ITERATIONS = 10_000
_RANK = (O.OK, O.RATE_LIMITED, O.GUARD_HIT, O.MALFORMED, O.TIMEOUT, O.SERVER_ERROR)
_C = contract()
MIN, PROBATION = _C.risk_min_samples(), _C.risk_probation_min_samples()
WEIGHTS = _C.risk_weights()


def _tracker_with(seq: list[O]):  # type: ignore[no-untyped-def]
    tracker = risk_tracker(Clock(1_000.0))
    for i, outcome in enumerate(seq):
        tracker.observe("m", outcome, f"org{i % 3}")
    return tracker


def test_property_7_score_bounds_and_floor() -> None:
    rng = random.Random(SEED)
    for i in range(ITERATIONS):
        n = rng.randint(1, 3 * MIN)
        seq = [rng.choice(_RANK) for _ in range(n)]
        reading = _tracker_with(seq).score("m")
        assert 0.0 <= reading.score <= 1.0, f"seed={SEED} i={i}"
        assert reading.total == n, f"seed={SEED} i={i}"
        if n < MIN:
            assert reading.score == 0.0, f"seed={SEED} i={i} scored below the floor"
        else:
            expected = min(1.0, sum(WEIGHTS[o] for o in seq) / n)
            assert abs(reading.score - expected) < 1e-12, f"seed={SEED} i={i}"


def test_property_7_monotone_in_weighted_errors_at_fixed_total() -> None:
    rng = random.Random(SEED + 1)
    for i in range(ITERATIONS):
        n = rng.randint(MIN, 3 * MIN)
        base = [rng.choice(_RANK) for _ in range(n)]
        worse = list(base)
        for _ in range(rng.randint(1, 5)):
            j = rng.randrange(n)
            worse[j] = rng.choice([o for o in _RANK if WEIGHTS[o] >= WEIGHTS[worse[j]]])
        assert sum(WEIGHTS[o] for o in worse) >= sum(WEIGHTS[o] for o in base)
        assert _tracker_with(worse).score("m").score >= _tracker_with(base).score("m").score, (
            f"seed={SEED + 1} i={i} a heavier mix scored lower"
        )


def test_property_7_old_samples_leave_the_window() -> None:
    rng = random.Random(SEED + 2)
    for i in range(1_000):
        clock = Clock(1_000.0)
        tracker = risk_tracker(clock)
        observe_many(tracker, "m", O.SERVER_ERROR, ["a", "b", "c"], MIN)
        assert tracker.score("m").score == 1.0
        clock.now += _C.risk_window_s() + rng.uniform(0.0, 100.0)
        assert tracker.score("m").total == 0 and tracker.score("m").score == 0.0, (
            f"seed={SEED + 2} i={i}"
        )


def test_property_7_probation_uses_only_post_recovery_samples() -> None:
    rng = random.Random(SEED + 3)
    for i in range(1_000):
        tracker = risk_tracker(Clock(1_000.0))
        observe_many(tracker, "m", O.SERVER_ERROR, ["a", "b", "c"], MIN)
        tracker.reset_for_recovery("m")
        assert tracker.score("m").total == 0 and tracker.score("m").on_probation
        k = rng.randint(1, PROBATION - 1)
        observe_many(tracker, "m", O.SERVER_ERROR, ["a"], k)
        assert tracker.score("m").score == 0.0, f"seed={SEED + 3} i={i} below probation floor"
        observe_many(tracker, "m", O.SERVER_ERROR, ["a"], PROBATION - k)
        assert tracker.score("m").score == 1.0, f"seed={SEED + 3} i={i} probation floor reached"
        observe_many(tracker, "m", O.OK, ["a"], MIN)  # min_samples post-reset samples end probation
        assert not tracker.score("m").on_probation, f"seed={SEED + 3} i={i}"


def test_pinned_cases_for_429_and_5xx() -> None:
    thr = _C.risk_threshold()
    for outcome, rate, trips in (
        (O.RATE_LIMITED, 0.5, False),  # 50% 429 scores 0.25
        (O.RATE_LIMITED, 1.0, True),  # 100% 429 scores 0.5
        (O.SERVER_ERROR, 0.5, True),
        (O.TIMEOUT, 0.1, False),
        (O.GUARD_HIT, 0.5, False),
    ):
        tracker = risk_tracker(Clock(1_000.0))
        errors = int(MIN * rate)
        observe_many(tracker, "m", outcome, ["a", "b", "c"], errors)
        observe_many(tracker, "m", O.OK, ["a", "b", "c"], MIN - errors)
        score = tracker.score("m").score
        assert (score >= thr) is trips, f"{outcome} at {rate}: {score}"
    assert abs(WEIGHTS[O.RATE_LIMITED] * 0.5 - 0.25) < 1e-12
