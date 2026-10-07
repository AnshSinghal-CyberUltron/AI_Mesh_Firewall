"""GW05c phase 3b — the kill switch moves with the delta instead of being rebuilt.

`test_a_round_with_no_changes_reads_nothing` and `test_cold_start_is_proportional_to_engaged`
are the cost guards. The fail-closed battery is the part that matters more: an engaged switch
that does not reach a worker is the one failure this component must never have.
"""

from __future__ import annotations

import inspect

import pytest

from gateway_v2.admit.killswitch import (
    KillSwitchSnapshot,
    KillSwitchState,
    engaged_scopes,
    scope_is_on,
)
from gateway_v2.domain.state import SignedRecord, StateKind, StateOp, StoreDataUnavailable, Version
from gateway_v2.runtime.state_sig import make_record

SECRET = b"gw05c-ks-test-secret"


def _ks(
    scope: str,
    *,
    on: bool,
    seq: int,
    feed_seq: int,
    deleted: bool = False,
    body: dict[str, object] | None = None,
) -> SignedRecord:
    return make_record(
        SECRET,
        StateKind.KS,
        scope,
        {"on": on} if body is None else body,
        Version(1, seq),
        feed_seq,
        deleted=deleted,
        op=StateOp.REVOKE if deleted else StateOp.PUT,
    )


def _fresh(**kwargs: object) -> KillSwitchSnapshot:
    return KillSwitchSnapshot(stale_ms=5_000, clock=lambda: 100.0, **kwargs)  # type: ignore[arg-type]


# --- cost shape -------------------------------------------------------------------------------


def test_the_snapshot_structurally_cannot_read_the_store() -> None:
    """The strongest available guard: no store, reader or feed can be handed to it.

    RC2's refresh owned a store connection and re-read the whole kind. This snapshot only ever
    receives records someone else already selected, so an O(tenants) read is not expressible
    here. A future parameter named like a store makes this fail.
    """
    parameters = set(inspect.signature(KillSwitchSnapshot.__init__).parameters) - {"self"}

    assert parameters == {"stale_ms", "clock"}
    for method in ("apply", "adopt", "state", "org_killed", "model_killed", "view"):
        source = inspect.getsource(getattr(KillSwitchSnapshot, method))
        assert "await" not in source, f"{method} must not perform I/O"


def test_a_round_with_no_changes_keeps_the_snapshot_fresh() -> None:
    """RC2 spent 203 ms of loop time on this round, twice a second, at 25k tenants."""
    snap = _fresh()
    snap.adopt(
        [_ks("org:acme", on=True, seq=1, feed_seq=1)],
        attested_feed_seq=1,
        attested_engaged=1,
        now=100.0,
    )

    snap.apply([], attested_feed_seq=1, attested_engaged=1, now=100.1)

    assert snap.state(100.1) is KillSwitchState.OK
    assert snap.org_killed("acme") is True
    assert snap.view().count == 1
    assert snap.age_seconds(100.1) == 0.0, "an empty round still proves freshness"


def test_cold_start_is_built_only_from_the_engaged_records_given() -> None:
    """Two engaged scopes out of 25,000 records: the snapshot sees two."""
    snap = _fresh()

    snap.adopt(
        [
            _ks("org:acme", on=True, seq=1, feed_seq=24_999),
            _ks("model:gpt-4o", on=True, seq=2, feed_seq=25_000),
        ],
        attested_feed_seq=25_000,
        attested_engaged=2,
        now=100.0,
    )

    view = snap.view()
    assert view.orgs == frozenset({"acme"})
    assert view.models == frozenset({"gpt-4o"})
    assert view.count == 2
    assert view.feed_seq == 25_000, "the cursor reaches the attested position in one adopt"


# --- fail closed ------------------------------------------------------------------------------


def test_an_engaged_switch_missing_from_the_published_set_is_unavailable() -> None:
    """THE fail-open case: the manifest attests an engaged scope the set does not name."""
    snap = _fresh()

    with pytest.raises(StoreDataUnavailable, match="holds 1 engaged scopes"):
        snap.adopt(
            [_ks("org:acme", on=True, seq=1, feed_seq=1)],
            attested_feed_seq=1,
            attested_engaged=2,
            now=100.0,
        )


def test_drift_after_a_delta_is_unavailable() -> None:
    """The O(1) integrity check that replaces re-verifying the whole set every round."""
    snap = _fresh()
    snap.adopt([], attested_feed_seq=0, attested_engaged=0, now=100.0)

    with pytest.raises(StoreDataUnavailable, match="the manifest attests 3"):
        snap.apply(
            [_ks("org:acme", on=True, seq=1, feed_seq=1)],
            attested_feed_seq=1,
            attested_engaged=3,
            now=100.1,
        )


def test_a_truncated_round_does_not_check_the_count() -> None:
    """A partial view legitimately disagrees with the manifest; it must not fail closed."""
    snap = _fresh()
    snap.adopt([], attested_feed_seq=0, attested_engaged=0, now=100.0)

    snap.apply(
        [_ks("org:acme", on=True, seq=1, feed_seq=1)],
        attested_feed_seq=1,
        attested_engaged=None,
        now=100.1,
    )

    assert snap.org_killed("acme") is True


def test_an_ambiguous_body_is_unavailable_not_off() -> None:
    """The entire reason C36 stores an explicit OFF record."""
    snap = _fresh()

    for body in ({}, {"on": "true"}, {"on": 1}, {"on": None}):
        with pytest.raises(StoreDataUnavailable):
            snap.apply(
                [_ks("org:acme", on=True, seq=1, feed_seq=1, body=body)],
                attested_feed_seq=1,
                now=100.0,
            )


def test_an_unknown_scope_is_unavailable_not_ignored() -> None:
    snap = _fresh()

    with pytest.raises(StoreDataUnavailable, match="not recognised"):
        snap.apply([_ks("region:eu", on=True, seq=1, feed_seq=1)], attested_feed_seq=1)


def test_an_empty_scope_name_is_unavailable() -> None:
    snap = _fresh()

    with pytest.raises(StoreDataUnavailable, match="names nothing"):
        snap.apply([_ks("org:", on=True, seq=1, feed_seq=1)], attested_feed_seq=1)


def test_a_non_killswitch_record_is_unavailable() -> None:
    record = make_record(SECRET, StateKind.PLAN, "org-a", {"on": True}, Version(1, 1), 1)

    with pytest.raises(StoreDataUnavailable, match="is not a kill switch"):
        scope_is_on(record)


def test_a_snapshot_that_has_never_been_verified_is_stale() -> None:
    assert _fresh().state(100.0) is KillSwitchState.STALE


def test_a_snapshot_past_the_ceiling_is_stale() -> None:
    snap = _fresh()
    snap.adopt([], attested_feed_seq=0, attested_engaged=0, now=100.0)

    assert snap.state(104.9) is KillSwitchState.OK
    assert snap.state(105.1) is KillSwitchState.STALE


# --- the engaged set moves correctly -----------------------------------------------------------


def test_engaging_and_disengaging_by_delta() -> None:
    snap = _fresh()
    snap.adopt([], attested_feed_seq=0, attested_engaged=0, now=100.0)

    snap.apply(
        [_ks("org:acme", on=True, seq=1, feed_seq=1)],
        attested_feed_seq=1,
        attested_engaged=1,
        now=100.1,
    )
    assert snap.org_killed("acme") is True

    snap.apply(
        [_ks("org:acme", on=False, seq=2, feed_seq=2)],
        attested_feed_seq=2,
        attested_engaged=0,
        now=100.2,
    )
    assert snap.org_killed("acme") is False
    assert snap.view().count == 0


def test_the_global_switch_is_separate_from_org_and_model() -> None:
    snap = _fresh()
    snap.adopt(
        [
            _ks("global", on=True, seq=1, feed_seq=1),
            _ks("org:acme", on=True, seq=2, feed_seq=2),
        ],
        attested_feed_seq=2,
        attested_engaged=2,
        now=100.0,
    )

    assert snap.state(100.0) is KillSwitchState.ENGAGED
    assert snap.view().engaged is True
    assert snap.view().orgs == frozenset({"acme"})
    assert snap.view().count == 2


def test_a_deleted_scope_record_disengages() -> None:
    snap = _fresh()
    snap.adopt(
        [_ks("org:acme", on=True, seq=1, feed_seq=1)],
        attested_feed_seq=1,
        attested_engaged=1,
        now=100.0,
    )

    snap.apply(
        [_ks("org:acme", on=True, seq=2, feed_seq=2, deleted=True)],
        attested_feed_seq=2,
        attested_engaged=0,
        now=100.1,
    )

    assert snap.org_killed("acme") is False


def test_a_record_above_the_attested_position_is_not_applied() -> None:
    """A publish in flight must not engage a switch the manifest has not attested yet."""
    snap = _fresh()
    snap.adopt([], attested_feed_seq=0, attested_engaged=0, now=100.0)

    snap.apply(
        [
            _ks("org:acme", on=True, seq=1, feed_seq=1),
            _ks("org:ahead", on=True, seq=2, feed_seq=9),
        ],
        attested_feed_seq=1,
        attested_engaged=1,
        now=100.1,
    )

    assert snap.org_killed("acme") is True
    assert snap.org_killed("ahead") is False


def test_a_replayed_engage_is_idempotent() -> None:
    snap = _fresh()
    snap.adopt([], attested_feed_seq=0, attested_engaged=0, now=100.0)
    record = _ks("org:acme", on=True, seq=1, feed_seq=1)

    snap.apply([record], attested_feed_seq=1, attested_engaged=1, now=100.1)
    snap.apply([record], attested_feed_seq=1, attested_engaged=1, now=100.2)

    assert snap.view().count == 1


def test_org_and_model_names_may_contain_a_colon() -> None:
    snap = _fresh()
    snap.adopt(
        [_ks("model:vendor:gpt-4o:preview", on=True, seq=1, feed_seq=1)],
        attested_feed_seq=1,
        attested_engaged=1,
        now=100.0,
    )

    assert snap.model_killed("vendor:gpt-4o:preview") is True


def test_engaged_scopes_filters_by_position_and_state() -> None:
    records = [
        _ks("org:on", on=True, seq=1, feed_seq=1),
        _ks("org:off", on=False, seq=2, feed_seq=2),
        _ks("org:ahead", on=True, seq=3, feed_seq=9),
    ]

    assert engaged_scopes(records, 5) == ("org:on",)
