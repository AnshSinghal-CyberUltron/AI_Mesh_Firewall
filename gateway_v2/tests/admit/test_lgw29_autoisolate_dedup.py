# Validates: Requirements 5.7, 5.8 (AC26, AC27: events per process, one dedup key; recover once)
"""Auto-isolation events: N workers -> N events with one dedup key; RECOVER once; signed silent."""

from __future__ import annotations

from gateway_v2.domain.incident import IncidentEventKind as K
from gateway_v2.domain.incident import IsolationOrigin, ModelOutcome
from tests.admit._lgw29_support import Rig, iso_body, ks, load


def test_eight_workers_tripping_on_one_model_emit_eight_events_with_one_dedup_key() -> None:
    rigs = [Rig() for _ in range(8)]
    for rig in rigs:
        rig.trip()
        assert rig.isolator.evaluate("a") == "isolated"
    events = [e for rig in rigs for e in rig.events]
    assert len(events) == 8 and {e.kind for e in events} == {K.ISOLATE}
    assert {e.dedup_key for e in events} == {"a:auto"}
    assert all(e.origin is IsolationOrigin.AUTO and e.fallback == "b" for e in events)


def test_a_local_isolation_emits_recover_exactly_once_and_enters_probation() -> None:
    rig = Rig()
    rig.trip()
    assert rig.isolator.evaluate("a") == "isolated"
    rig.isolator.sweep()  # becomes known-active
    rig.wall.now += 400.0  # past auto_isolation_duration_s() (300 s)
    rig.mono.now += 400.0
    rig.isolator.sweep()
    rig.isolator.sweep()
    recovers = [e for e in rig.events if e.kind is K.RECOVER]
    assert len(recovers) == 1 and recovers[0].dedup_key == "a:auto"
    reading = rig.tracker.score("a")
    assert reading.on_probation and reading.total == 0  # the stale window never counts again
    assert rig.metrics.series()["amf_incident_recover_total"] == 1.0


def test_signed_isolations_emit_nothing_from_the_gateway_but_reset_probation() -> None:
    rigs = [Rig() for _ in range(8)]
    for rig in rigs:
        load(rig.snap, [ks("iso:a", body=iso_body(fallback="b", expires_at=rig.wall.now + 30),
                           feed_seq=1)])
        rig.trip()
        rig.isolator.sweep()
        rig.wall.now += 31
        rig.isolator.sweep()
    assert sum(len(rig.events) for rig in rigs) == 0  # control owns the signed transition
    assert all(rig.tracker.score("a").on_probation for rig in rigs)


def test_a_signed_record_superseding_a_local_isolation_emits_no_recover() -> None:
    rig = Rig()
    rig.trip()
    rig.isolator.evaluate("a")
    rig.isolator.sweep()
    load(rig.snap, [ks("iso:a", body=iso_body(origin="auto", fallback="b",
                                              expires_at=rig.wall.now + 999), feed_seq=1)])
    rig.isolator.sweep()
    assert [e.kind for e in rig.events] == [K.ISOLATE]


def test_skipped_event_is_emitted_once_per_trip_not_per_request() -> None:
    rig = Rig(fallbacks={})
    rig.trip()
    results = [rig.isolator.evaluate("a") for _ in range(5)]
    assert results[0] == "skipped:no_fallback" and set(results[1:]) == {"none"}
    assert [e.kind for e in rig.events] == [K.AUTO_ISOLATE_SKIPPED]
    # the trip ends (score drops below the threshold), then a NEW trip may be reported again
    rig.mono.now += 1_000.0
    rig.tracker.observe("a", ModelOutcome.OK, "o1")
    rig.isolator.evaluate("a")
    rig.trip()
    assert rig.isolator.evaluate("a") == "skipped:no_fallback" and len(rig.events) == 2
