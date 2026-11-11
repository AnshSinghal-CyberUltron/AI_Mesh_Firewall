# Feature: gw29-killswitch-isolation, Property 3: STALE, never loaded or drifted is never admitted
# Validates: Requirements 1.3, 1.5 (fail closed on stale, never-loaded and manifest drift)
"""P3: for random engaged sets and random manifest drift, a non-fresh snapshot reports STALE."""

from __future__ import annotations

import random

import pytest

from gateway_v2.domain.state import StoreDataUnavailable
from tests.admit._lgw29_support import Clock, ks, snapshot

SEED = 29003
ITERATIONS = 10_000
_SCOPES = ("global", "org:a", "org:b", "model:m1", "model:m2", "om:a:m1", "om:b:m2", "iso:m1")


def _records(rng: random.Random) -> list:
    keys = [k for k in _SCOPES if rng.random() < 0.5]
    out = []
    for i, key in enumerate(keys, start=1):
        body = {"on": True, "expires_at": 9e9} if key.startswith("iso:") else None
        out.append(ks(key, feed_seq=i, body=body))
    return out


def test_property_3_never_loaded_is_stale() -> None:
    rng = random.Random(SEED)
    for i in range(ITERATIONS):
        snap = snapshot(Clock(rng.uniform(0, 1e6)))
        ev = snap.evaluate(rng.choice("ab"), rng.choice(("m1", "m2")))
        assert ev.stale, f"seed={SEED} i={i}"


def test_property_3_drift_and_aging_are_stale_and_a_clean_round_recovers() -> None:
    rng = random.Random(SEED + 1)
    for i in range(ITERATIONS):
        clock = Clock(rng.uniform(0, 1e6))
        snap = snapshot(clock)
        records = _records(rng)
        top = len(records)
        drift = rng.choice((-2, -1, 1, 2))
        with pytest.raises(StoreDataUnavailable):
            snap.apply(records, attested_feed_seq=top, attested_engaged=len(records) + drift
                       if len(records) + drift >= 0 else len(records) + 1)
        assert snap.evaluate("a", "m1").stale, f"seed={SEED + 1} i={i} drift admitted"
        snap.apply(records, attested_feed_seq=top, attested_engaged=len(records))
        assert not snap.evaluate("a", "m1").stale, f"seed={SEED + 1} i={i} clean round"
        clock.now += rng.choice((5.001, 6.0, 60.0, 1e4))
        assert snap.evaluate("a", "m1").stale, f"seed={SEED + 1} i={i} aged out"


def test_property_3_a_fresh_snapshot_reports_exactly_the_engaged_scopes() -> None:
    rng = random.Random(SEED + 2)
    for i in range(ITERATIONS):
        snap = snapshot(Clock())
        records = _records(rng)
        engaged = {r.key for r in records}
        snap.apply(records, attested_feed_seq=len(records), attested_engaged=len(records))
        ev = snap.evaluate("a", "m1")
        assert not ev.stale, f"seed={SEED + 2} i={i}"
        assert ev.global_engaged == ("global" in engaged), f"seed={SEED + 2} i={i}"
        assert ev.org_engaged == ("org:a" in engaged), f"seed={SEED + 2} i={i}"
        assert ev.org_model_engaged == ("om:a:m1" in engaged), f"seed={SEED + 2} i={i}"
        assert ev.model_engaged == ("model:m1" in engaged), f"seed={SEED + 2} i={i}"
