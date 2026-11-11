# Validates: Requirements 3, 6 (one read model, one expiry decision, local auto isolations)
"""`IncidentState`: expiry rules, signed-vs-local, tightening-only add_local, sweep transitions."""

from __future__ import annotations

from tests.admit._lgw29_support import Clock, iso_body, ks, load, local_auto, make_state, snapshot


def _world(*, slots: int = 4, hook: object = None, auto_s: float = 300.0, hold_s: float = 600.0):
    mono, wall = Clock(100.0), Clock(1_000.0)
    snap = snapshot(mono)
    return snap, make_state(snap, wall, mono, slots=slots, hook=hook, auto_s=auto_s,
                            hold_s=hold_s), wall, mono


def _put(snap, key: str, feed_seq: int, **body: object) -> None:
    load(snap, [ks(key, body=iso_body(**body), feed_seq=feed_seq)])


def _count(snap) -> int:
    return snap.view().count


# -- expiry ----------------------------------------------------------------------------------


def test_operator_isolation_with_a_deadline_expires_at_read_time_without_grace() -> None:
    snap, state, wall, _ = _world()
    load(snap, [ks("iso:m", body=iso_body(fallback="f", expires_at=1_050.0), feed_seq=1)])
    assert state.active_isolation("m") is not None
    wall.now = 1_049.999
    assert state.active_isolation("m") is not None
    wall.now = 1_050.0  # wall() >= expires_at: inactive
    assert state.active_isolation("m") is None
    assert snap.isolation_record("m") is not None  # the signed set is untouched: expiry is at READ


def test_operator_no_expiry_stays_active_across_any_clock_jump() -> None:
    snap, state, wall, mono = _world()
    _put(snap, "iso:m", 1, expires_at=None, no_expiry=True)
    wall.now += 1e9
    mono.now += 1e9
    assert state.active_isolation("m") is not None


def test_auto_record_with_a_deadline_follows_the_deadline() -> None:
    snap, state, wall, _ = _world()
    _put(snap, "iso:m", 1, origin="auto", created_at=1_000.0, expires_at=1_100.0)
    assert state.active_isolation("m") is not None
    wall.now = 1_100.0
    assert state.active_isolation("m") is None


def test_auto_record_without_a_deadline_uses_created_at_plus_duration() -> None:
    snap, state, wall, _ = _world(auto_s=300.0)
    _put(snap, "iso:m", 1, origin="auto", created_at=1_000.0)
    wall.now = 1_299.0
    assert state.active_isolation("m") is not None
    wall.now = 1_300.0
    assert state.active_isolation("m") is None


def test_auto_record_with_neither_uses_monotonic_first_seen() -> None:
    snap, state, wall, mono = _world(auto_s=300.0)
    _put(snap, "iso:m", 1, origin="auto", expires_at="garbage")
    assert state.active_isolation("m") is not None  # first seen now
    wall.now -= 1e6  # the wall clock is irrelevant here
    mono.now += 299.0
    assert state.active_isolation("m") is not None
    mono.now += 1.0
    assert state.active_isolation("m") is None


def test_the_monotonic_hold_cap_defeats_an_absurd_or_skewed_deadline() -> None:
    snap, state, wall, mono = _world(auto_s=300.0, hold_s=600.0)
    _put(snap, "iso:m", 1, origin="auto", created_at=1_000.0, expires_at=9e12)
    assert state.active_isolation("m") is not None
    mono.now += 599.0
    wall.now = 0.5  # the wall clock went backwards; the deadline is still "in the future"
    assert state.active_isolation("m") is not None
    mono.now += 1.0
    assert state.active_isolation("m") is None


def test_an_operator_record_is_not_subject_to_the_hold_cap() -> None:
    snap, state, wall, mono = _world(hold_s=600.0)
    _put(snap, "iso:m", 1, expires_at=9e12)
    mono.now += 1e6
    wall.now = 1_001.0
    assert state.active_isolation("m") is not None


def test_a_malformed_isolation_is_active_for_that_model_only() -> None:
    snap, state, _, _ = _world()
    _put(snap, "iso:bad", 1, origin="bogus")
    rec = state.active_isolation("bad")
    assert rec is not None and rec.malformed
    assert state.active_isolation("other") is None


def test_re_isolating_after_an_off_resets_the_first_seen_clock() -> None:
    snap, state, _, mono = _world(auto_s=300.0, hold_s=600.0)
    _put(snap, "iso:m", 1, origin="auto", expires_at="x")
    mono.now += 250.0
    assert state.active_isolation("m") is not None
    load(snap, [ks("iso:m", on=False, feed_seq=2)])
    state.sweep()
    mono.now += 100.0
    _put(snap, "iso:m", 3, origin="auto", expires_at="x")
    mono.now += 250.0  # 350 s since the first record but only 250 s since this one
    assert state.active_isolation("m") is not None


# -- signed vs local -------------------------------------------------------------------------


def test_signed_is_preferred_and_local_is_used_when_signed_is_inactive() -> None:
    snap, state, wall, _ = _world()
    assert state.add_local(local_auto("m", wall.now, fallback="loc"))
    assert state.active_isolation("m").fallback == "loc"  # type: ignore[union-attr]
    _put(snap, "iso:m", 1, origin="auto", expires_at=wall.now + 50, fallback="sig")
    assert state.active_isolation("m").fallback == "sig"  # type: ignore[union-attr]
    wall.now += 51  # the signed record expired; the local one (300 s) has not
    assert state.active_isolation("m").fallback == "loc"  # type: ignore[union-attr]


def test_status_of_reports_kill_at_any_scope_and_the_active_isolation() -> None:
    snap, state, _, _ = _world()
    load(snap, [ks("om:a:m", feed_seq=1), ks("iso:m", body=iso_body(fallback="f", expires_at=9e9),
                                              feed_seq=2)])
    hit = state.status_of("a", "m", snap.evaluate("a", "m"))
    assert hit.killed and hit.isolation is not None
    miss = state.status_of("b", "m", snap.evaluate("b", "m"))
    assert not miss.killed and miss.isolation is not None


# -- add_local: tightening only --------------------------------------------------------------


def test_add_local_refuses_over_an_active_operator_isolation_and_counts() -> None:
    snap, state, wall, _ = _world()
    _put(snap, "iso:m", 1, fallback="f", expires_at=wall.now + 500)
    assert not state.add_local(local_auto("m", wall.now))
    assert state.local_suppressed_total == 1
    assert state.active_isolation("m").origin.value == "operator"  # type: ignore[union-attr]


def test_add_local_refuses_over_a_model_kill() -> None:
    snap, state, wall, _ = _world()
    load(snap, [ks("model:m", feed_seq=1)])
    assert not state.add_local(local_auto("m", wall.now))
    assert state.active_isolation("m") is None


def test_add_local_is_allowed_when_the_signed_record_has_expired() -> None:
    snap, state, wall, _ = _world()
    _put(snap, "iso:m", 1, fallback="f", expires_at=wall.now + 10)
    wall.now += 11
    assert state.add_local(local_auto("m", wall.now))


def test_add_local_refuses_a_duplicate_and_non_auto_records() -> None:
    from gateway_v2.domain.incident import IsolationOrigin, IsolationRecord

    _, state, wall, _ = _world()
    assert state.add_local(local_auto("m", wall.now))
    assert not state.add_local(local_auto("m", wall.now))
    operator = IsolationRecord("n", "f", IsolationOrigin.OPERATOR, "", None, 9e9, False, 0, False)
    assert not state.add_local(operator)


def test_add_local_at_the_bound_evicts_the_earliest_expiry_and_counts() -> None:
    _, state, wall, _ = _world(slots=2)
    assert state.add_local(local_auto("a", wall.now, dur=100))
    assert state.add_local(local_auto("b", wall.now, dur=50))
    assert state.add_local(local_auto("c", wall.now, dur=200))
    assert state.local_evicted_total == 1
    assert state.active_isolation("b") is None  # earliest expiry went
    assert state.active_isolation("a") is not None and state.active_isolation("c") is not None


def test_expired_local_entries_are_purged_on_write_before_evicting() -> None:
    _, state, wall, _ = _world(slots=1)
    assert state.add_local(local_auto("a", wall.now, dur=10))
    wall.now += 11
    assert state.add_local(local_auto("b", wall.now))
    assert state.local_evicted_total == 0


# -- operator OFF clears local auto state ----------------------------------------------------


def test_a_signed_off_clears_the_local_entry_and_calls_the_probation_hook() -> None:
    calls: list[str] = []
    snap, state, wall, _ = _world(hook=calls.append)
    _put(snap, "iso:m", 1, fallback="f", expires_at=wall.now + 999)
    assert state.active_isolation("m") is not None  # the state has now SEEN the signed record
    load(snap, [ks("iso:m", on=False, feed_seq=2)])
    assert state.add_local(local_auto("m", wall.now))  # allowed again: nothing signed is active
    assert state.active_isolation("m") is not None
    # a later signed ON then OFF clears what auto-logic created in between
    _put(snap, "iso:m", 3, fallback="f", expires_at=wall.now + 999)
    state.active_isolation("m")
    load(snap, [ks("iso:m", on=False, feed_seq=4)])
    assert state.active_isolation("m") is None
    assert calls == ["m"]


# -- sweep -----------------------------------------------------------------------------------


def test_sweep_reports_a_local_expiry_exactly_once() -> None:
    _, state, wall, _ = _world()
    assert state.add_local(local_auto("m", wall.now, dur=60))
    assert state.sweep().local_expired == ()  # first sweep: becomes known-active
    wall.now += 61
    first = state.sweep()
    assert (first.local_expired, first.signed_expired) == (("m",), ())
    assert state.sweep().local_expired == ()


def test_sweep_reports_a_signed_expiry_separately_and_once() -> None:
    snap, state, wall, _ = _world()
    _put(snap, "iso:m", 1, fallback="f", expires_at=wall.now + 30)
    state.sweep()
    wall.now += 31
    result = state.sweep()
    assert (result.local_expired, result.signed_expired) == ((), ("m",))
    assert state.sweep().signed_expired == ()


def test_sweep_reports_a_signed_off_as_a_signed_end() -> None:
    snap, state, wall, _ = _world()
    _put(snap, "iso:m", 1, fallback="f", expires_at=wall.now + 999)
    state.sweep()
    load(snap, [ks("iso:m", on=False, feed_seq=2)])
    assert state.sweep().signed_expired == ("m",)


def test_a_signed_record_superseding_a_local_one_converges_silently() -> None:
    snap, state, wall, _ = _world()
    assert state.add_local(local_auto("m", wall.now))
    state.sweep()
    _put(snap, "iso:m", 1, origin="auto", fallback="f", expires_at=wall.now + 999)
    result = state.sweep()
    assert (result.local_expired, result.signed_expired) == ((), ())
    assert state.snapshot_models()[0].source == "signed"


def test_sweep_state_is_bounded_and_purges_stale_first_seen() -> None:
    snap, state, wall, _ = _world(slots=2)
    for i in range(20):
        _put(snap, f"iso:m{i}", i + 1, origin="auto", expires_at="x")
        state.active_isolation(f"m{i}")
        load(snap, [ks(f"iso:m{i}", on=False, feed_seq=100 + i)])
        state.sweep()
    assert len(state._first_seen) == 0  # noqa: SLF001 - bounded-growth assertion
    assert len(state._was_active) == 0  # noqa: SLF001
    assert len(state._signed_seen) == 0  # noqa: SLF001
    assert wall.now == 1_000.0


# -- display read model ----------------------------------------------------------------------


def test_snapshot_models_lists_only_active_isolations_sorted_with_source() -> None:
    snap, state, wall, _ = _world()
    load(snap, [ks("iso:zeta", body=iso_body(fallback="f", expires_at=wall.now + 99), feed_seq=1),
                ks("iso:old", body=iso_body(fallback="f", expires_at=wall.now - 1), feed_seq=2)])
    assert state.add_local(local_auto("alpha", wall.now))
    views = state.snapshot_models()
    assert [(v.model, v.source) for v in views] == [("alpha", "local"), ("zeta", "signed")]
    assert state.expired_signed_count() == 1
    assert _count(snap) == 2
