# Validates: Requirements 1, 3, 4 (scope grammar, org-model, isolation map, drift latch)
"""`KillSwitchSnapshot` GW29 extension: grammar, evaluate(), drift latch, quarantine, AC15."""

from __future__ import annotations

import inspect
from types import MappingProxyType

import pytest

from gateway_v2.admit.killswitch import KillSwitchSnapshot, KillSwitchState
from gateway_v2.domain.incident import IsolationOrigin
from gateway_v2.domain.state import StoreDataUnavailable
from tests.admit._lgw29_support import Clock, iso_body, ks, load, snapshot


def test_each_scope_engages_exactly_what_it_names() -> None:
    snap = snapshot()
    load(snap, [ks("org:acme", feed_seq=1), ks("model:m1", feed_seq=2),
                ks("om:beta:m2", feed_seq=3)], on_count=3)
    assert snap.evaluate("acme", "other").org_engaged
    assert not snap.evaluate("beta", "other").org_engaged
    assert snap.evaluate("zzz", "m1").model_engaged
    assert not snap.evaluate("zzz", "m2").model_engaged
    hit = snap.evaluate("beta", "m2")
    assert hit.org_model_engaged and not hit.org_engaged and not hit.model_engaged
    assert not snap.evaluate("acme", "m2").org_model_engaged  # another org, same model
    assert not snap.evaluate("beta", "m3").org_model_engaged  # same org, another model


def test_global_scope_reports_engaged_state_and_flag() -> None:
    snap = snapshot()
    load(snap, [ks("global", feed_seq=1)], on_count=1)
    ev = snap.evaluate("a", "m")
    assert ev.global_engaged and ev.state == KillSwitchState.ENGAGED.value and not ev.stale


def test_explicit_off_record_readmits() -> None:
    snap = snapshot()
    load(snap, [ks("om:a:m", feed_seq=1)], on_count=1)
    assert snap.evaluate("a", "m").org_model_engaged
    load(snap, [ks("om:a:m", on=False, feed_seq=2)], on_count=0)
    assert not snap.evaluate("a", "m").org_model_engaged
    assert snap.view().count == 0


def test_engage_and_disengage_in_one_refresh_ends_disengaged() -> None:  # AC6
    snap = snapshot()
    load(snap, [ks("model:m", feed_seq=1), ks("model:m", on=False, feed_seq=2)], on_count=0)
    assert not snap.evaluate("a", "m").model_engaged and snap.state() is KillSwitchState.OK


def test_deleted_record_disengages() -> None:
    snap = snapshot()
    load(snap, [ks("iso:m", body=iso_body(expires_at=9e9), feed_seq=1)], on_count=1)
    assert snap.isolation_record("m") is not None
    load(snap, [ks("iso:m", on=False, deleted=True, feed_seq=2)], on_count=0)
    assert snap.isolation_record("m") is None and snap.isolation_models() == ()


# -- drift latch (C3) ------------------------------------------------------------------------


def test_drift_is_stale_immediately_not_after_the_ceiling() -> None:
    clock = Clock()
    snap = snapshot(clock)
    load(snap, [], on_count=0)
    assert snap.state() is KillSwitchState.OK
    with pytest.raises(StoreDataUnavailable):
        snap.apply([ks("org:a", feed_seq=1)], attested_feed_seq=1, attested_engaged=5)
    assert snap.state() is KillSwitchState.STALE  # same clock tick: no 5 s grace
    assert snap.evaluate("a", "m").stale and snap.refresh_age_seconds() is None
    load(snap, [ks("org:a", feed_seq=1)], on_count=1)  # a later clean round re-arms
    assert snap.state() is KillSwitchState.OK


@pytest.mark.parametrize(
    "bad",
    [
        ks("tenant:x", feed_seq=1),  # unknown shape
        ks("om:onlyslug", feed_seq=1),  # known prefix, no model separator
        ks("om:bad slug:m", feed_seq=1),
        ks("org:a", feed_seq=1, body={"on": "yes"}),  # malformed `on`
        ks("iso:m", feed_seq=1, body={"nope": 1}),  # no `on`
    ],
)
def test_unrecognised_or_malformed_records_latch_stale(bad: object) -> None:
    snap = snapshot()
    load(snap, [], on_count=0)
    with pytest.raises(StoreDataUnavailable):
        snap.apply([bad], attested_feed_seq=1)  # type: ignore[list-item]
    assert snap.state() is KillSwitchState.STALE


# -- quarantine (C19) ------------------------------------------------------------------------


def test_bad_model_segment_is_quarantined_counted_and_never_matches() -> None:
    snap = snapshot()
    load(snap, [ks("model:bad name", feed_seq=1), ks("om:a:bad name", feed_seq=2),
                ks("iso:bad name", body=iso_body(expires_at=9e9), feed_seq=3)], on_count=3)
    view = snap.view()
    assert view.quarantined == 3 and view.count == 3
    assert snap.state() is KillSwitchState.OK  # no fleet-wide 503
    assert not snap.evaluate("a", "bad name").model_engaged
    assert snap.isolation_models() == ()
    load(snap, [ks("model:bad name", on=False, feed_seq=4)], on_count=2)
    assert snap.view().quarantined == 2


# -- model identity at read ------------------------------------------------------------------


def test_alias_and_case_variants_are_one_engaged_scope() -> None:
    aliases = MappingProxyType({"gpt-4o-2024-08-06": "gpt-4o"})
    snap = snapshot(aliases=aliases)
    load(snap, [ks("model:GPT-4o", feed_seq=1), ks("model:gpt-4o-2024-08-06", feed_seq=2)],
         on_count=2)  # two RECORDS, one engaged family: on_count counts records
    assert snap.evaluate("a", "gpt-4o").model_engaged
    assert snap.view().models == frozenset({"gpt-4o"})
    load(snap, [ks("model:GPT-4o", on=False, feed_seq=3)], on_count=1)
    assert snap.evaluate("a", "gpt-4o").model_engaged  # the alias record still holds it
    load(snap, [ks("model:gpt-4o-2024-08-06", on=False, feed_seq=4)], on_count=0)
    assert not snap.evaluate("a", "gpt-4o").model_engaged


def test_isolation_record_is_parsed_with_the_alias_map() -> None:
    aliases = MappingProxyType({"fast": "gpt-4o-mini"})
    snap = snapshot(aliases=aliases)
    load(snap, [ks("iso:GPT-4o", body=iso_body(fallback="fast", origin="auto", created_at=5.0),
                   feed_seq=1)], on_count=1)
    rec = snap.isolation_record("gpt-4o")
    assert rec is not None and rec.fallback == "gpt-4o-mini"
    assert rec.origin is IsolationOrigin.AUTO and not rec.malformed
    assert snap.view().isolations == 1 and snap.isolation_records() == (rec,)


def test_a_malformed_isolation_is_kept_malformed_not_raised() -> None:
    snap = snapshot()
    load(snap, [ks("iso:m", body=iso_body(origin="bogus"), feed_seq=1)], on_count=1)
    rec = snap.isolation_record("m")
    assert rec is not None and rec.malformed and snap.state() is KillSwitchState.OK


def test_isolations_count_toward_on_count_whether_or_not_expired() -> None:
    snap = snapshot()
    load(snap, [ks("iso:m", body=iso_body(expires_at=1.0), feed_seq=1)], on_count=1)
    assert snap.view().count == 1 and snap.isolation_record("m") is not None  # expiry is at READ


def test_adopt_resets_every_new_collection() -> None:
    snap = snapshot()
    load(snap, [ks("om:a:m", feed_seq=1), ks("iso:m", body=iso_body(expires_at=9e9), feed_seq=2)],
         on_count=2)
    snap.adopt([], attested_feed_seq=2, attested_engaged=0)
    view = snap.view()
    assert (view.org_models, view.isolations, view.quarantined, view.count) == (0, 0, 0, 0)


def test_records_above_the_attested_position_are_not_applied() -> None:
    snap = snapshot()
    snap.apply([ks("om:a:m", feed_seq=9)], attested_feed_seq=5, attested_engaged=0)
    assert not snap.evaluate("a", "m").org_model_engaged


# -- single lock + staleness inputs ----------------------------------------------------------


def test_evaluate_takes_the_lock_once() -> None:
    source = inspect.getsource(KillSwitchSnapshot.evaluate)
    assert source.count("with self._lock") == 1
    assert "await" not in source and "store" not in source.lower().replace("stamp", "")


def test_evaluate_staleness_matches_state() -> None:
    clock = Clock()
    snap = snapshot(clock)
    assert snap.evaluate("a", "m").stale  # never loaded
    load(snap, [], on_count=0)
    assert not snap.evaluate("a", "m").stale
    clock.now += 5.001
    assert snap.evaluate("a", "m").stale and snap.state() is KillSwitchState.STALE


# -- AC15: old applier, new record ------------------------------------------------------------


def _pre_gw29_set_scope(scope: str) -> None:
    """The pre-GW29 recogniser, copied verbatim in behaviour: three shapes, anything else raises."""
    if scope == "global" or scope.startswith(("org:", "model:")):
        return
    raise StoreDataUnavailable(f"kill-switch scope {scope!r} is not recognised")


@pytest.mark.parametrize("scope", ["om:a:m", "iso:m"])
def test_an_old_gateway_goes_stale_on_the_new_scopes_so_rollout_order_is_mandatory(
    scope: str,
) -> None:
    with pytest.raises(StoreDataUnavailable):
        _pre_gw29_set_scope(scope)
    snap = snapshot()  # the new gateway accepts them
    load(snap, [ks(scope, body=iso_body(expires_at=9e9) if scope.startswith("iso") else None,
                   feed_seq=1)], on_count=1)
    assert snap.state() is KillSwitchState.OK


@pytest.mark.parametrize("scope", ["global", "org:acme", "model:gpt-4o"])
def test_the_three_original_scopes_behave_as_before(scope: str) -> None:
    _pre_gw29_set_scope(scope)
    snap = snapshot()
    load(snap, [ks(scope, feed_seq=1)], on_count=1)
    assert snap.view().count == 1
    load(snap, [ks(scope, on=False, feed_seq=2)], on_count=0)
    assert snap.view().count == 0
