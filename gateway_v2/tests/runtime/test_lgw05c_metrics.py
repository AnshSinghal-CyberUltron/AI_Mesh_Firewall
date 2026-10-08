"""GW05c phase 5 — metric cardinality independent of the tenant count.

`test_the_series_set_is_identical_at_three_and_twenty_five_thousand_tenants` is the requirement
stated as a test. RC2 exported one series per tenant and rebuilt the label set on the serving
loop: 50,003 series per worker at 50,000 tenants (C28), a shared-memory gauge directory that
overflows near 1,000 orgs, and — once full — HTTP 500 on 40% of new tenants' first requests
(R2-10).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import pytest

from gateway_v2.admit.identity import IdentityCache, identity_applier
from gateway_v2.admit.killswitch import KillSwitchSnapshot, KillSwitchState, killswitch_applier
from gateway_v2.domain.plan import StreamingMode
from gateway_v2.domain.state import Cursor, StateKind, Version
from gateway_v2.plan.delta import plan_applier
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedReader
from gateway_v2.runtime.state_metrics import (
    PREFIX,
    SERIES_COUNT,
    KindMetrics,
    StateMetricsRecorder,
)
from gateway_v2.runtime.state_nudge import ListenerCounters
from gateway_v2.runtime.state_sig import make_record
from gateway_v2.runtime.state_task import DeltaBudget, RoundReport, StateSynchroniser
from tests.plan.test_lgw05c_delta import _draft
from tests.runtime.test_lgw05c_feed import SECRET, FakeStore


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


def _wire(tenants: int) -> tuple[StateSynchroniser, StateMetricsRecorder, FakeStore]:
    store = FakeStore()
    for position in range(1, tenants + 1):
        org = f"org-{position}"
        store.publish(
            make_record(
                SECRET, StateKind.PLAN, org, _plan_body(org), Version(1, position), position,
            ),
            count=position,
        )
    plans = PlanStore()
    snapshot = ReplicaSnapshot(plans, clock=lambda: 1.0)
    switches = KillSwitchSnapshot(stale_ms=5_000, clock=lambda: 100.0)
    keys = IdentityCache(clock=lambda: 0.0)
    sync = StateSynchroniser(
        FeedReader(store, SECRET),
        {
            StateKind.PLAN: plan_applier(plans, snapshot, clock=lambda: 1.0),
            StateKind.KS: killswitch_applier(switches),
            StateKind.KEY: identity_applier(keys),
        },
        budget=DeltaBudget(records=100_000),
    )
    recorder = StateMetricsRecorder()
    report = _run(sync.drain(StateKind.PLAN))
    recorder.observe_round(report)
    recorder.observe_identity(keys.stats())
    recorder.observe_killswitch(
        switches.view(), stale=switches.state(100.0) is KillSwitchState.STALE,
    )
    recorder.observe_nudge(ListenerCounters())
    return sync, recorder, store


# --- the requirement -----------------------------------------------------------------------------


def test_the_series_set_is_identical_at_three_and_twenty_five_thousand_tenants() -> None:
    """GW05c: 'Metric cardinality independent of tenant count'."""
    _s, small, _a = _wire(3)
    _t, large, _b = _wire(25_000)

    assert set(small.snapshot().series()) == set(large.snapshot().series())
    assert len(large.snapshot().series()) == len(small.snapshot().series())


def test_the_series_count_is_a_constant() -> None:
    _s, recorder, _a = _wire(1_000)

    assert len(recorder.snapshot().series()) == SERIES_COUNT
    # 9 per kind (8 round metrics + GW05b's floor), 15 scalars, 8 freshness.
    assert SERIES_COUNT == 9 * len(StateKind) + 15 + 8


def test_no_series_name_carries_a_tenant_identifier() -> None:
    """The failure mode is a label whose domain is tenant-derived, not a big number."""
    _s, recorder, _a = _wire(500)

    names = recorder.snapshot().series()

    assert all(name.startswith(PREFIX) for name in names)
    for name in names:
        assert "org-" not in name, name
        assert "org=" not in name, name
        assert "tenant" not in name, name
        assert "hash-" not in name, name
    # The only label in the whole surface is `kind`, and its domain is a four-member enum.
    labelled = [name for name in names if "{" in name]
    assert {name.split("{")[1] for name in labelled} == {
        f'kind="{kind.value}"}}' for kind in StateKind
    }


def test_the_engaged_scopes_are_counted_not_named() -> None:
    """RC2 emitted killswitch_engaged{scope,key} -- one series per engaged scope."""
    switches = KillSwitchSnapshot(stale_ms=5_000, clock=lambda: 100.0)
    records = tuple(
        make_record(SECRET, StateKind.KS, f"org:t{n}", {"on": True}, Version(1, n), n)
        for n in range(1, 41)
    )
    switches.adopt(records, attested_feed_seq=40, attested_engaged=40, now=100.0)
    recorder = StateMetricsRecorder()

    recorder.observe_killswitch(
        switches.view(), stale=switches.state(100.0) is KillSwitchState.STALE,
    )
    series = recorder.snapshot().series()

    assert series[f"{PREFIX}_killswitch_engaged_total"] == 40
    assert not [name for name in series if "t1" in name or "scope" in name]


def test_a_tenant_keyed_mapping_cannot_appear_in_a_reading() -> None:
    """Structural: every field of a reading is a scalar or the closed-enum kind map."""
    _s, recorder, _a = _wire(200)
    reading = recorder.snapshot()

    assert len(reading.by_kind) <= len(StateKind)
    assert set(reading.by_kind).issubset(set(StateKind))
    for group in (reading.identity, reading.killswitch, reading.nudge):
        for value in vars(type(group)).get("__slots__", ()):
            assert isinstance(getattr(group, value), int), value
    for metrics in reading.by_kind.values():
        for field in KindMetrics.__slots__:
            assert isinstance(getattr(metrics, field), int), field


# --- what the numbers say -------------------------------------------------------------------------


def test_a_round_is_recorded_without_reading_anything() -> None:
    sync, recorder, store = _wire(50)
    store.reset_counters()

    report = _run(sync.round_once(StateKind.PLAN))
    recorder.observe_round(report)

    assert store.records_read == 0, "recording a metric must not touch the store"
    series = recorder.snapshot().series()
    assert series[f'{PREFIX}_rounds_total{{kind="plan"}}'] == 2


def test_lag_is_the_aggregate_answer_that_replaced_per_tenant_versions() -> None:
    recorder = StateMetricsRecorder()
    behind = RoundReport(
        kind=StateKind.PLAN,
        cursor=Cursor(Version(1, 40), 40),
        applied=40,
        truncated=True,
        offloaded=True,
    )

    recorder.observe_round(behind, attested=100)
    series = recorder.snapshot().series()

    assert series[f'{PREFIX}_cursor{{kind="plan"}}'] == 40
    assert series[f'{PREFIX}_attested{{kind="plan"}}'] == 100
    assert series[f'{PREFIX}_lag{{kind="plan"}}'] == 60
    assert series[f'{PREFIX}_rounds_truncated_total{{kind="plan"}}'] == 1
    assert series[f'{PREFIX}_rounds_offloaded_total{{kind="plan"}}'] == 1


def test_lag_never_reads_negative() -> None:
    """A counter that publishes a negative rate is R2-11; a gauge must not either."""
    assert KindMetrics(cursor=90, attested=50).lag == 0


def test_a_failed_round_is_counted_separately_from_a_round() -> None:
    recorder = StateMetricsRecorder()
    failed = RoundReport(
        kind=StateKind.KS,
        cursor=Cursor(Version(1, 1), 1),
        applied=0,
        truncated=False,
        offloaded=False,
        error="ks manifest missing",
    )

    recorder.observe_round(failed)
    series = recorder.snapshot().series()

    assert series[f'{PREFIX}_rounds_total{{kind="ks"}}'] == 1
    assert series[f'{PREFIX}_rounds_failed_total{{kind="ks"}}'] == 1
    assert series[f'{PREFIX}_records_applied_total{{kind="ks"}}'] == 0


def test_the_cursor_gauge_only_rises() -> None:
    recorder = StateMetricsRecorder()
    for position in (5, 9, 7):
        recorder.observe_round(
            RoundReport(
                kind=StateKind.PLAN,
                cursor=Cursor(Version(1, position), position),
                applied=1,
                truncated=False,
                offloaded=False,
            ),
        )

    assert recorder.snapshot().series()[f'{PREFIX}_cursor{{kind="plan"}}'] == 9


def test_identity_counters_come_from_the_cache_itself() -> None:
    cache = IdentityCache(capacity=4, clock=lambda: 0.0)
    cache.mark_applied(3)
    for position in range(10):
        cache._admit(f"k{position}", _principal(position))
    recorder = StateMetricsRecorder()

    recorder.observe_identity(cache.stats())
    series = recorder.snapshot().series()

    assert series[f"{PREFIX}_identity_held"] == 4, "the bound is visible"
    assert series[f"{PREFIX}_identity_evicted_total"] == 6
    assert series[f"{PREFIX}_identity_cursor"] == 3


def test_a_stale_killswitch_is_visible() -> None:
    switches = KillSwitchSnapshot(stale_ms=5_000, clock=lambda: 100.0)
    recorder = StateMetricsRecorder()

    recorder.observe_killswitch(
        switches.view(), stale=switches.state(100.0) is KillSwitchState.STALE,
    )

    assert switches.state(100.0) is KillSwitchState.STALE
    assert recorder.snapshot().series()[f"{PREFIX}_killswitch_stale"] == 1


def test_nudge_counters_are_carried_through() -> None:
    recorder = StateMetricsRecorder()

    recorder.observe_nudge(
        ListenerCounters(subscribed=3, dead=2, nudges=9, nudge_failures=1, messages=40, pings=5),
    )
    series = recorder.snapshot().series()

    assert series[f"{PREFIX}_nudge_dead_total"] == 2, "a non-zero dead count is the D2 alarm"
    assert series[f"{PREFIX}_nudge_failures_total"] == 1


def test_an_unobserved_kind_still_has_its_series() -> None:
    """A missing series reads as 'no data'; a zero reads as 'nothing happened'."""
    recorder = StateMetricsRecorder()

    series = recorder.snapshot().series()

    assert len(series) == SERIES_COUNT
    assert series[f'{PREFIX}_rounds_total{{kind="budget"}}'] == 0


def _principal(position: int) -> object:
    from gateway_v2.domain.identity import Principal

    return Principal(
        key_id=f"id-{position}", org_id="org-a", rate_per_s=1.0, burst=1.0, feed_seq=3,
    )


@pytest.mark.parametrize("tenants", [1, 100, 5_000])
def test_cardinality_holds_at_every_scale(tenants: int) -> None:
    _s, recorder, _a = _wire(tenants)

    assert len(recorder.snapshot().series()) == SERIES_COUNT
