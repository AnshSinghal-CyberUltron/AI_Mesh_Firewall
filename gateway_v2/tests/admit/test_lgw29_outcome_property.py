# Feature: gw29-killswitch-isolation, Property 14: distinct-org rule and bounded tracking
# Validates: Requirements 5.4, 5.5 (one tenant cannot isolate a model for everyone)
"""P14, tracker half: `distinct_error_orgs >= min(floor, distinct_orgs_seen)`; untracked names."""

from __future__ import annotations

import random

from gateway_v2.admit.auto_isolation import IsolatorParams  # noqa: F401 - import smoke
from gateway_v2.domain.incident import ModelOutcome as O
from tests.admit._lgw29_support import Clock, contract, risk_tracker

SEED = 29014
ITERATIONS = 2_000
_C = contract()
FLOOR, MIN = _C.risk_min_distinct_orgs(), _C.risk_min_samples()


def _scenario(n_active: int, n_error_orgs: int, rng: random.Random):  # type: ignore[no-untyped-def]
    tracker = risk_tracker(Clock(1_000.0))
    orgs = [f"org{i}" for i in range(n_active)]
    bad = set(rng.sample(orgs, n_error_orgs))
    for _ in range(MIN * 2):
        org = rng.choice(orgs)
        tracker.observe("m", O.SERVER_ERROR if org in bad else O.OK, org)
    for org in orgs:  # make sure every active org contributed at least one sample
        tracker.observe("m", O.SERVER_ERROR if org in bad else O.OK, org)
    return tracker.score("m")


def test_property_14_distinct_org_rule_matches_the_specification() -> None:
    rng = random.Random(SEED)
    for i in range(ITERATIONS):
        n_active = rng.randint(1, 6)
        n_error = rng.randint(1, n_active)
        reading = _scenario(n_active, n_error, rng)
        tag = f"seed={SEED} i={i} active={n_active} error_orgs={n_error}"
        assert reading.distinct_orgs_seen == min(n_active, FLOOR), tag
        assert reading.distinct_error_orgs == min(n_error, FLOOR), tag
        trips = reading.distinct_error_orgs >= min(FLOOR, reading.distinct_orgs_seen)
        assert trips == (min(n_error, FLOOR) >= min(n_active, FLOOR)), tag


def test_the_four_documented_cases() -> None:
    rng = random.Random(SEED + 1)

    def trips(n_active: int, n_error: int) -> bool:
        r = _scenario(n_active, n_error, rng)
        return r.distinct_error_orgs >= min(FLOOR, r.distinct_orgs_seen)

    assert trips(1, 1)  # one active org may trip alone
    assert not trips(2, 1)  # two active, errors from one: the other org is healthy evidence
    assert not trips(FLOOR + 1, FLOOR - 1)  # 3+ active, errors from fewer than the floor
    assert trips(FLOOR + 1, FLOOR)  # 3+ active with errors from the floor


def test_untracked_names_never_occupy_slots_and_the_table_is_bounded() -> None:
    known = {"a", "b"}
    tracker = risk_tracker(Clock(1_000.0), max_models=2, known=known.__contains__)
    for i in range(1_000):
        tracker.observe(f"garbage-{i}", O.SERVER_ERROR, "o")
    assert tracker.untracked_total == 1_000 and tracker.readings() == ()
    tracker.observe("a", O.OK, "o")
    tracker.observe("b", O.OK, "o")
    assert [r.model for r in tracker.readings()] == ["a", "b"]
    full = risk_tracker(Clock(1_000.0), max_models=2)
    for name in ("x", "y", "z", "w"):
        full.observe(name, O.OK, "o")
    assert len(full.readings()) == 2 and full.untracked_total == 2


def test_observe_never_raises_on_bad_input() -> None:
    tracker = risk_tracker(Clock(1_000.0))
    tracker.observe("m", "not-an-outcome", "o")  # type: ignore[arg-type]
    tracker.observe(None, O.OK, "o")  # type: ignore[arg-type]
    tracker.observe("m", O.OK, None)  # type: ignore[arg-type]
    assert tracker.untracked_total == 2 and tracker.score("m").total == 1


def test_org_ids_are_never_stored() -> None:
    tracker = risk_tracker(Clock(1_000.0))
    tracker.observe("m", O.SERVER_ERROR, "super-secret-org-name")
    ring = tracker._rings["m"]  # noqa: SLF001
    stored = {t for bucket in ring.err_orgs + ring.seen_orgs for t in bucket}
    assert stored and all("secret" not in t for t in stored)
