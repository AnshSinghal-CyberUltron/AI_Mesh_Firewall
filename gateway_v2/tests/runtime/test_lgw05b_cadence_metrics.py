"""GW05b phase 6: see freshness, and bound the knobs that control it.

Three things, each with a reason it is not cosmetic:

* The recheck cadence is what bounds a NEW process's extra start-up at one re-hydrator round
  plus 100 ms. It only ever shortens the wait of a process that is not serving, so the steady
  state -- the thing R2-02 was about -- is untouched.
* The metric surface stays a FIXED expression. R2-10 is the reason: a full metric directory made
  40% of new tenants' first requests fail with HTTP 500, so a series whose existence depends on
  tenant count must not be addable by accident. `amf_state_floor` is per kind, and `kind` is a
  four-member enum.
* Every knob relationship is a fail-at-START check. C36's measured cost was a background store
  timeout EQUAL to the staleness ceiling -- each value reasonable alone, the pair fatal, and
  nothing validated the pair.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable

import pytest

from gateway_v2.domain.locks import FRESH_MS, PG_GRACE_MS
from gateway_v2.domain.state import Cursor, StateKind, Version
from gateway_v2.domain.state_knobs import (
    DEFAULT_KNOBS,
    StateKnobs,
)
from gateway_v2.runtime.state_feed import FeedReader, FeedRound
from gateway_v2.runtime.state_metrics import (
    ABSENT,
    PREFIX,
    SERIES_COUNT,
    StateMetricsRecorder,
)
from gateway_v2.runtime.state_sig import encode_stamp, make_stamp
from gateway_v2.runtime.state_stamp import RECHECK_S, StampView
from gateway_v2.runtime.state_task import DeltaBudget, RoundReport, StateSynchroniser
from tests.runtime.test_lgw05c_feed import SECRET, FakeStore, _seed

BY = "rehydrator-a:4711"


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _raw(
    moment: float,
    *,
    deep: bool = False,
    degraded: bool = False,
    plan_at: int = 1,
) -> bytes:
    cursors = {kind: Cursor(Version(1, 1), 1) for kind in StateKind}
    cursors[StateKind.PLAN] = Cursor(Version(1, plan_at), plan_at)
    return encode_stamp(
        make_stamp(SECRET, moment, cursors, BY, deep=deep, degraded=degraded),
    )


def _view(*, now: float = 100.0, started_at: float = 99.0) -> StampView:
    moment = [now]
    return StampView(SECRET, fresh_ms=5_000, started_at=started_at, clock=lambda: moment[0])


# --- the recheck cadence -------------------------------------------------------------------------


def test_an_unverified_process_rechecks_faster_than_its_period() -> None:
    """Up to a full period of start-up saved, for a process that is serving nothing anyway."""
    view = _view()

    assert view.fresh() is False
    assert view.recheck_s(0.5, store_answered=True) == RECHECK_S


def test_a_verified_process_waits_its_normal_period() -> None:
    """The steady state must be untouched: this is the 99.9% case R2-02 was measured in."""
    view = _view()
    view.observe(_raw(100.0))

    assert view.fresh() is True
    assert view.recheck_s(0.5, store_answered=True) == 0.5


def test_a_process_that_cannot_reach_the_store_waits_its_normal_period() -> None:
    """Hammering an unreachable store is how a partition becomes a thundering herd."""
    view = _view()

    assert view.recheck_s(0.5, store_answered=False) == 0.5


def test_the_recheck_never_lengthens_a_short_period() -> None:
    view = _view()

    assert view.recheck_s(0.05, store_answered=True) == 0.05


def test_the_synchroniser_exposes_the_cadence_to_its_caller() -> None:
    """There is no loop in here on purpose: the serving process owns its own scheduling."""
    store = FakeStore()
    _seed(store, 2)
    view = _view()
    sync = _sync(store, view)
    store.stamp_raw = _raw(100.0, plan_at=2)

    reports = _run(sync.drain_all())

    assert sync.next_delay_s(reports, period_s=0.5) == 0.5, "fresh: normal period"


def test_the_cadence_shortens_while_the_store_answers_but_no_stamp_lands() -> None:
    store = FakeStore()
    _seed(store, 2)
    sync = _sync(store, _view())

    reports = _run(sync.drain_all())

    assert all(report.ok for report in reports), "the store answered"
    assert sync.next_delay_s(reports, period_s=0.5) == RECHECK_S


def test_a_synchroniser_without_a_stamp_always_waits_its_period() -> None:
    store = FakeStore()
    _seed(store, 1)
    sync = _sync(store, None)

    reports = _run(sync.drain_all())

    assert sync.next_delay_s(reports, period_s=0.5) == 0.5


def _sync(store: FakeStore, view: StampView | None) -> StateSynchroniser:
    def nothing(round_: FeedRound) -> None:
        del round_

    return StateSynchroniser(
        FeedReader(store, SECRET),
        {StateKind.PLAN: nothing},
        budget=DeltaBudget(records=100),
        stamp=view,
    )


# --- transition-only logging ---------------------------------------------------------------------


def test_becoming_verified_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    view = _view()

    with caplog.at_level(logging.INFO, logger="amf.state.freshness"):
        assert view.note_freshness() is False
        view.observe(_raw(100.0))
        assert view.note_freshness() is True
        for _ in range(5):
            assert view.note_freshness() is None, "no transition, no line"

    verified = [r for r in caplog.records if "state_verified" in r.getMessage()]
    assert len(verified) == 1
    assert BY in verified[0].getMessage()


def test_losing_freshness_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    moment = [100.0]
    view = StampView(SECRET, fresh_ms=5_000, started_at=99.0, clock=lambda: moment[0])
    view.observe(_raw(100.0))
    view.note_freshness()

    moment[0] = 110.0
    with caplog.at_level(logging.WARNING, logger="amf.state.freshness"):
        assert view.note_freshness() is False
        for _ in range(5):
            assert view.note_freshness() is None

    unverified = [r for r in caplog.records if "state_unverified" in r.getMessage()]
    assert len(unverified) == 1
    assert "last verified against Postgres" in unverified[0].getMessage()


def test_a_new_processes_first_wait_is_not_counted_as_a_lapse() -> None:
    """Otherwise every deploy and every autoscale event looks like an incident."""
    view = _view()

    view.note_freshness()
    view.note_freshness()

    assert view.lapses == 0


def test_a_real_lapse_is_counted() -> None:
    moment = [100.0]
    view = StampView(SECRET, fresh_ms=5_000, started_at=99.0, clock=lambda: moment[0])
    view.observe(_raw(100.0))
    view.note_freshness()

    moment[0] = 110.0
    view.note_freshness()

    assert view.lapses == 1

    view.observe(_raw(110.0))
    view.note_freshness()
    moment[0] = 120.0
    view.note_freshness()

    assert view.lapses == 2


def test_the_cycle_notes_freshness_without_the_request_path_doing_it() -> None:
    """`fresh()` is read per REQUEST, so a transition log must not live there (C31)."""
    store = FakeStore()
    _seed(store, 1)
    view = _view()
    sync = _sync(store, view)
    store.stamp_raw = _raw(100.0, plan_at=1)

    _run(sync.drain_all())

    assert view.lapses == 0
    for _ in range(100):
        view.fresh()  # the request path, a hundred times over
    assert view.lapses == 0, "reading freshness must not move any counter"


# --- the metric surface --------------------------------------------------------------------------


def test_the_series_count_still_matches_the_fixed_expression() -> None:
    recorder = StateMetricsRecorder()

    assert len(recorder.snapshot().series()) == SERIES_COUNT
    assert SERIES_COUNT == 9 * len(StateKind) + 15 + 8


def test_every_freshness_series_is_present_before_anything_happens() -> None:
    """A gauge that appears only once it has a value cannot be alarmed on."""
    series = StateMetricsRecorder().snapshot().series()

    for name in (
        "stamp_fresh",
        "stamp_age_seconds",
        "stamp_verified_at_seconds",
        "stamp_deep_age_seconds",
        "stamp_degraded",
        "stamp_missing_total",
        "stamp_invalid_total",
        "unverified_transitions_total",
    ):
        assert f"{PREFIX}_{name}" in series, name
    for kind in StateKind:
        assert f'{PREFIX}_floor{{kind="{kind.value}"}}' in series


def test_an_unseen_age_reads_as_the_absent_sentinel() -> None:
    recorder = StateMetricsRecorder()
    recorder.observe_freshness(_view())

    series = recorder.snapshot().series()

    assert series[f"{PREFIX}_stamp_age_seconds"] == ABSENT
    assert series[f"{PREFIX}_stamp_deep_age_seconds"] == ABSENT
    assert series[f"{PREFIX}_stamp_fresh"] == 0


def test_a_freshness_reading_lands_in_the_series() -> None:
    moment = [105.0]
    view = StampView(SECRET, fresh_ms=5_000, started_at=99.0, clock=lambda: moment[0])
    view.observe(_raw(103.0, deep=True, plan_at=40))
    view.observe(None)
    view.observe(b"{not a stamp")
    recorder = StateMetricsRecorder()

    recorder.observe_freshness(view)
    series = recorder.snapshot().series()

    assert series[f"{PREFIX}_stamp_fresh"] == 1
    assert series[f"{PREFIX}_stamp_age_seconds"] == pytest.approx(2.0)
    assert series[f"{PREFIX}_stamp_verified_at_seconds"] == 103.0
    assert series[f"{PREFIX}_stamp_deep_age_seconds"] == pytest.approx(2.0)
    assert series[f"{PREFIX}_stamp_missing_total"] == 1
    assert series[f"{PREFIX}_stamp_invalid_total"] == 1
    assert series[f'{PREFIX}_floor{{kind="plan"}}'] == 40


def test_a_degraded_stamp_is_visible_as_such() -> None:
    """An operator must be able to tell a verified round from a Postgres ride-through."""
    view = _view()
    view.observe(_raw(100.0, degraded=True))
    recorder = StateMetricsRecorder()

    recorder.observe_freshness(view)

    assert recorder.snapshot().series()[f"{PREFIX}_stamp_degraded"] == 1


def test_a_negative_age_is_representable_so_clock_skew_can_be_alarmed_on() -> None:
    """The one dependency this design cannot remove, so it must at least be visible.

    A stamp from the future means the gateway's clock and the re-hydrator's have drifted apart.
    Clamping the gauge at zero would hide exactly the condition that extends the exposure
    window.
    """
    moment = [100.0]
    view = StampView(SECRET, fresh_ms=5_000, started_at=99.0, clock=lambda: moment[0])
    view.observe(_raw(130.0))
    recorder = StateMetricsRecorder()

    recorder.observe_freshness(view)
    series = recorder.snapshot().series()

    assert series[f"{PREFIX}_stamp_age_seconds"] == pytest.approx(-30.0)
    assert series[f"{PREFIX}_stamp_fresh"] == 0, "and it is not treated as fresh"


def test_the_series_set_does_not_grow_with_the_number_of_stamps_seen() -> None:
    view = _view()
    recorder = StateMetricsRecorder()
    recorder.observe_freshness(view)
    before = set(recorder.snapshot().series())

    for position in range(1, 500):
        view.observe(_raw(100.0, plan_at=position))
        recorder.observe_freshness(view)

    assert set(recorder.snapshot().series()) == before


def test_no_freshness_series_names_a_rehydrator() -> None:
    """`by` is operator-facing but unbounded: it belongs in a log line, never in a label."""
    view = _view()
    view.observe(_raw(100.0))
    recorder = StateMetricsRecorder()
    recorder.observe_freshness(view)

    assert all(BY not in name for name in recorder.snapshot().series())


def test_a_round_report_and_a_freshness_reading_compose() -> None:
    recorder = StateMetricsRecorder()
    recorder.observe_round(
        RoundReport(
            StateKind.PLAN, Cursor(Version(1, 9), 9), 9, truncated=False, offloaded=False,
        ),
    )
    view = _view()
    view.observe(_raw(100.0, plan_at=12))
    recorder.observe_freshness(view)

    series = recorder.snapshot().series()

    assert series[f'{PREFIX}_cursor{{kind="plan"}}'] == 9
    assert series[f'{PREFIX}_floor{{kind="plan"}}'] == 12
    assert len(series) == SERIES_COUNT


# --- the knobs -----------------------------------------------------------------------------------


def test_the_defaults_come_from_the_owner_locks() -> None:
    assert DEFAULT_KNOBS.fresh_ms == FRESH_MS
    assert DEFAULT_KNOBS.pg_grace_ms == PG_GRACE_MS
    assert DEFAULT_KNOBS.enforced is True
    assert DEFAULT_KNOBS.warnings == ()


def test_a_freshness_bound_under_two_rehydrator_periods_is_refused() -> None:
    """A stamp is up to one period plus one round old before a gateway can read it."""
    with pytest.raises(ValueError, match="at least 2 x"):
        StateKnobs(fresh_ms=1_500, rehydrate_period_ms=1_000, refresh_period_ms=200)


def test_a_refresh_period_over_half_the_bound_is_refused() -> None:
    """C36's measured defect: one slow cycle outlives the window it is meant to protect."""
    with pytest.raises(ValueError, match="at most half"):
        StateKnobs(fresh_ms=5_000, rehydrate_period_ms=1_000, refresh_period_ms=3_000)


def test_a_refresh_period_at_exactly_half_the_bound_is_allowed() -> None:
    assert StateKnobs(fresh_ms=5_000, refresh_period_ms=2_500).refresh_period_ms == 2_500


def test_a_grace_under_the_freshness_bound_is_refused() -> None:
    with pytest.raises(ValueError, match="at least"):
        StateKnobs(fresh_ms=5_000, pg_grace_ms=4_000)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"rehydrate_period_ms": 0}, "rehydrate_period_ms must be positive"),
        ({"refresh_period_ms": 0}, "refresh_period_ms must be positive"),
        ({"deep_every": 0}, "deep_every must be positive"),
        ({"fresh_ms": -1}, "fresh_ms must not be negative"),
        ({"pg_grace_ms": -1}, "pg_grace_ms must not be negative"),
    ],
)
def test_nonsense_periods_are_refused(kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        StateKnobs(**kwargs)


def test_freshness_can_be_disabled_and_says_so_loudly() -> None:
    """A break-glass, not a mode: it reinstates R2-03, so a start-up path must announce it."""
    knobs = StateKnobs(fresh_ms=0)

    assert knobs.enforced is False
    assert any("R2-03" in warning for warning in knobs.warnings)


def test_disabling_freshness_relaxes_the_relationships_it_governs() -> None:
    """With nothing enforced there is no window to keep coherent, so these must not reject."""
    knobs = StateKnobs(fresh_ms=0, rehydrate_period_ms=10_000, refresh_period_ms=9_000)

    assert knobs.enforced is False


def test_disabling_the_ride_through_says_so_loudly() -> None:
    knobs = StateKnobs(pg_grace_ms=0)

    assert any("Cloud SQL failover" in warning for warning in knobs.warnings)


def test_the_store_timeout_ceiling_is_derived_not_restated() -> None:
    """C36: a store op that outlives half a refresh period defeats the staleness window."""
    knobs = StateKnobs(refresh_period_ms=500)

    assert knobs.store_timeout_ceiling_s == 0.25


def test_the_enforcement_bound_is_derived_from_the_two_periods() -> None:
    """R2-03's invariant is stated in this number, so it is computed rather than written twice."""
    knobs = StateKnobs(rehydrate_period_ms=1_000, refresh_period_ms=500)

    assert knobs.enforcement_bound_s == 1.5


def test_the_first_round_is_not_a_deep_one() -> None:
    """The deep pass is O(records); charging start-up for it delays the very first stamp."""
    knobs = StateKnobs(deep_every=60)

    assert knobs.is_deep_round(1) is False
    assert knobs.is_deep_round(59) is False
    assert knobs.is_deep_round(60) is True
    assert knobs.is_deep_round(120) is True
    assert knobs.is_deep_round(0) is False


def test_the_knobs_are_immutable() -> None:
    knobs = StateKnobs()

    with pytest.raises(AttributeError):
        knobs.fresh_ms = 1  # type: ignore[misc]


def test_an_unenforced_deployment_does_not_poll_fast_forever() -> None:
    """With freshness off, `fresh()` is always true, so the fast recheck must never engage.

    Otherwise the break-glass would quietly cost every worker a 100 ms cycle for the lifetime
    of the process -- a performance regression hiding inside a safety opt-out.
    """
    view = StampView(SECRET, fresh_ms=0, started_at=99.0, clock=lambda: 100.0)

    assert view.fresh() is True
    assert view.recheck_s(0.5, store_answered=True) == 0.5


def test_an_unenforced_view_logs_no_transition_noise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    view = StampView(SECRET, fresh_ms=0, started_at=99.0, clock=lambda: 100.0)

    with caplog.at_level(logging.INFO, logger="amf.state.freshness"):
        first = view.note_freshness()
        for _ in range(5):
            assert view.note_freshness() is None

    assert first is True
    assert len([r for r in caplog.records if "state_verified" in r.getMessage()]) == 1
