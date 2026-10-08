"""R2-04 / GW05b — the re-hydrator PROCESS, its configuration and its metrics surface.

The runbook's remaining R2-04 clauses are statements about running processes:

> runs >= 2 re-hydrators in >= 2 zones (proven safe concurrently); and never lets an idle-stop
> policy touch them

plus the card bullet *"alarm when an `ok_publish_pending` write is older than one period"*. None of
them could be satisfied while `Rehydrator` was constructed only by tests. This file covers the
process: the env loader, one bounded round, the stop path, and the metric surface the three dormant
alarms in `deploy/observability/gw05b-state-freshness-alerts.yml` read.

No Postgres and no Valkey: the loop is driven against the in-memory twins through the real
`Rehydrator`, so what is exercised is the service's own behaviour rather than a mock of it.
`RehydratorService.build` is the only part that needs real adapters, and it is covered by the live
half of `test_r2_04_bounds.py` (the `verify_bounds()` call it makes before the first round).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from gateway_v2.domain.locks import FRESH_MS, PG_GRACE_MS
from gateway_v2.domain.state import StateKind
from gateway_v2.domain.state_knobs import StateKnobs
from state_control.db import KindCounters, MemoryControlDB
from state_control.metrics import (
    ALL_REASONS,
    SERIES_COUNT,
    RehydrationMetricsRecorder,
    render,
)
from state_control.publisher import MemoryStore
from state_control.rehydrate import (
    BOUND_LOCK,
    FAULT_CATEGORIES,
    FAULT_OUTAGE,
    MISSING,
    STORE_AHEAD,
    Rehydrator,
)
from state_control.service import RehydratorService, ServiceConfig, from_env
from state_control.writer import StateWriter

SECRET = b"r2-04-service-secret"

MINIMAL_ENV = {
    "AMF_STATE_PG_DSN": "postgresql://postgres:pg@127.0.0.1:5432/postgres",
    "AMF_STATE_VALKEY_URL": "redis://127.0.0.1:6379/0",
    "AMF_STATE_HMAC_KEY": "a-shared-signing-secret",
}


# --- the env loader -------------------------------------------------------------------------------


def test_the_minimal_environment_resolves_to_the_shipped_defaults() -> None:
    config = from_env(MINIMAL_ENV)

    assert config.knobs == StateKnobs()
    assert config.knobs.fresh_ms == FRESH_MS
    assert config.knobs.pg_grace_ms == PG_GRACE_MS
    assert config.lock_timeout_ms == 250
    assert config.statement_timeout_ms == 800
    assert config.secret == b"a-shared-signing-secret"


@pytest.mark.parametrize(
    "missing",
    ["AMF_STATE_PG_DSN", "AMF_STATE_VALKEY_URL", "AMF_STATE_HMAC_KEY"],
)
def test_a_missing_required_value_names_itself(missing: str) -> None:
    """A start-up failure must say which variable, not just that something is wrong."""
    env = {name: value for name, value in MINIMAL_ENV.items() if name != missing}

    with pytest.raises(ValueError, match=missing):
        from_env(env)


def test_the_signing_secret_explains_why_it_matters() -> None:
    """A mismatched secret fails every stamp's signature and fails the whole fleet closed, which
    is not something an operator should have to infer from a KeyError.
    """
    env = {name: value for name, value in MINIMAL_ENV.items() if name != "AMF_STATE_HMAC_KEY"}

    with pytest.raises(ValueError, match="match the gateways"):
        from_env(env)


def test_an_incoherent_knob_set_is_refused_at_start_up() -> None:
    """`StateKnobs` validates the relationships; the loader must not swallow that.

    A freshness bound shorter than two re-hydrator periods makes stamps that are born stale, so
    the fleet refuses continuously while every component reports healthy.
    """
    with pytest.raises(ValueError, match="at least 2 x"):
        from_env({**MINIMAL_ENV, "AMF_STATE_FRESH_MS": "1000"})


def test_a_non_numeric_knob_names_itself() -> None:
    with pytest.raises(ValueError, match="AMF_REHYDRATE_PERIOD_MS"):
        from_env({**MINIMAL_ENV, "AMF_REHYDRATE_PERIOD_MS": "soon"})


def test_the_rehydrator_id_defaults_to_host_and_pid_but_is_overridable() -> None:
    """Distinct per instance by default, which is what makes two containers distinguishable in
    the stamp's `by` field with no configuration at all (R2-04's HA clause).
    """
    assert ":" in from_env(MINIMAL_ENV).rehydrator_id
    assert from_env({**MINIMAL_ENV, "AMF_REHYDRATOR_ID": "rehydrator-a"}).rehydrator_id == (
        "rehydrator-a"
    )


def test_the_zone_is_recorded_so_an_operator_can_tell_the_instances_apart() -> None:
    assert from_env(MINIMAL_ENV).zone == "unknown"
    assert from_env({**MINIMAL_ENV, "AMF_DEPLOY_ZONE": "zone-b"}).zone == "zone-b"


def test_the_period_is_derived_not_restated() -> None:
    config = from_env({**MINIMAL_ENV, "AMF_REHYDRATE_PERIOD_MS": "2000"})

    assert config.period_s == 2.0
    assert config.knobs.fresh_ms >= 2 * config.knobs.rehydrate_period_ms


# --- the loop -------------------------------------------------------------------------------------


@dataclass
class _Lab:
    service: RehydratorService
    store: MemoryStore
    db: MemoryControlDB
    writer: StateWriter


def _service(**overrides: str) -> _Lab:
    config = from_env({**MINIMAL_ENV, **overrides})
    db = MemoryControlDB()
    store = MemoryStore()
    writer = StateWriter(db, store, SECRET)
    rehydrator = Rehydrator(
        db,
        store,
        writer,
        SECRET,
        stale_grace_s=0.0,
        name=config.rehydrator_id,
        fresh_ms=config.knobs.fresh_ms,
        pg_grace_ms=config.knobs.pg_grace_ms,
    )
    writer.plan_set("org-a", {"org_id": "org-a"})
    writer.key_add("hash-a", "org-a", key_id="k", rate_per_s=1.0, burst=1.0)
    writer.killswitch("global", on=False)
    writer.put(StateKind.BUDGET, "org-a", {"limit": 1})
    return _Lab(RehydratorService(config, rehydrator), store, db, writer)


def test_one_round_stamps_and_is_recorded() -> None:
    lab = _service()

    lab.service.round_once()

    assert lab.store.read_stamp() is not None
    series = lab.service.metrics().series()
    assert series["amf_rehydration_attempts_total"] == 1
    assert series["amf_rehydration_success_total"] == 1
    assert series["amf_state_stamp_written_total"] == 1
    assert series["amf_rehydrator_active"] == 1


def test_a_flushed_store_is_repaired_and_counted_by_reason() -> None:
    """The repair counters are what an operator trends; the reason is what explains it."""
    lab = _service()
    lab.service.round_once()
    lab.store.flush()

    lab.service.round_once()

    series = lab.service.metrics().series()
    assert series[f'amf_rehydration_repair_reason_total{{reason="{MISSING}"}}'] == len(StateKind)
    for kind in StateKind:
        assert series[f'amf_rehydration_repairs_total{{kind="{kind.value}"}}'] == 1


def test_the_deep_round_follows_the_declared_cadence() -> None:
    """Round 1 must NOT be deep: the deep pass is O(records) and charging start-up for it adds
    the whole estate to the time before the first stamp.
    """
    lab = _service(AMF_REHYDRATE_DEEP_EVERY="3")
    lab.store.reset_counters()

    for _ in range(3):
        lab.service.round_once()

    assert lab.store.index_scans == len(StateKind), "exactly one deep round in three"


def test_a_failed_round_is_counted_without_stopping_the_service() -> None:
    lab = _service()

    def unreachable(kind: StateKind) -> KindCounters:
        raise ConnectionError("injected postgres outage")

    lab.db.counters = unreachable  # type: ignore[method-assign]

    lab.service.round_once()  # must not raise

    series = lab.service.metrics().series()
    assert series["amf_rehydration_failure_total"] == 1
    assert series[f'amf_rehydration_fault_total{{category="{FAULT_OUTAGE}"}}'] == len(StateKind)


def test_stopping_clears_the_active_gauge() -> None:
    """`amf_rehydrator_active` going to 0 is how a clean shutdown is told from a crash."""
    lab = _service()
    lab.service.round_once()

    lab.service.stop()
    lab.service._recorder.stopping()  # noqa: SLF001 - run() does this in its finally block

    assert lab.service.metrics().series()["amf_rehydrator_active"] == 0


def test_a_break_glass_configuration_is_logged_loudly_by_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`fresh_ms=0` is a break-glass, not a mode. A silent break-glass is not a break-glass.

    `StateKnobs.warnings` returns the lines; this asserts `run()` actually emits them, which is
    the half that prose alone would leave unproven.
    """
    lab = _service(AMF_STATE_FRESH_MS="0")
    lab.service.stop()  # run() checks the stop event before the first round

    with caplog.at_level(logging.WARNING, logger="amf.state.service"):
        assert lab.service.run() == 0

    emitted = " ".join(record.getMessage() for record in caplog.records)
    assert "NOT enforced" in emitted, "the R2-03 reinstatement must be said out loud"


# --- the metric surface ---------------------------------------------------------------------------


def test_the_series_key_set_is_a_fixed_size() -> None:
    """R2-10: a full metric directory made 40% of new tenants' first requests return HTTP 500.

    So the surface must be a closed expression, not "whatever got emitted". Asserting the constant
    against the real key set is what makes a tenant-derived label fail the gate.
    """
    recorder = RehydrationMetricsRecorder()

    assert len(recorder.snapshot().series()) == SERIES_COUNT


def test_the_series_key_set_does_not_grow_with_the_estate() -> None:
    small = _service()
    small.service.round_once()

    big = _service()
    for position in range(200):
        big.writer.plan_set(f"org-{position}", {"org_id": f"org-{position}"})
    big.store.flush()
    big.service.round_once()

    assert big.service.metrics().series()["amf_rehydration_records_restored_total"
                                          '{kind="plan"}'] > 200
    assert set(small.service.metrics().series()) == set(big.service.metrics().series())


def test_every_fault_category_and_repair_reason_has_a_series_from_the_first_scrape() -> None:
    """A series that appears only once something breaks cannot be alarmed on."""
    series = RehydrationMetricsRecorder().snapshot().series()

    for category in FAULT_CATEGORIES:
        assert f'amf_rehydration_fault_total{{category="{category}"}}' in series
    for reason in ALL_REASONS:
        assert f'amf_rehydration_repair_reason_total{{reason="{reason}"}}' in series


def test_the_three_dormant_alarms_now_have_producers() -> None:
    """The exact series names `gw05b-state-freshness-alerts.yml` reads.

    `StatePublishPending` and `StateStampWithheld` were written against counters that nothing
    produced, so neither could ever fire. `amf_rehydrator_active` is what makes this instance
    visible to `StateRehydratorSingleInstance`'s `up` count.
    """
    series = RehydrationMetricsRecorder().snapshot().series()

    assert "amf_state_publish_pending_oldest_seconds" in series
    assert "amf_state_stamp_withheld_total" in series
    assert "amf_rehydrator_active" in series


def test_publish_pending_reports_zero_when_nothing_is_pending() -> None:
    """A healthy estate has a real answer to "how old is the oldest unpublished write", and it
    is zero -- not the "never happened" sentinel, which would make the alarm unevaluable.
    """
    recorder = RehydrationMetricsRecorder()
    recorder.observe_publish_pending(None)

    assert recorder.snapshot().series()["amf_state_publish_pending_oldest_seconds"] == 0.0

    recorder.observe_publish_pending(4.5)

    assert recorder.snapshot().series()["amf_state_publish_pending_oldest_seconds"] == 4.5


def test_a_bound_firing_is_counted_apart_from_an_outage() -> None:
    """The distinction R2-04 turns on: a held lock and a dead database are different incidents."""
    recorder = RehydrationMetricsRecorder()
    from state_control.rehydrate import RoundSummary

    recorder.observe_round(
        RoundSummary(
            repairs=(),
            healthy=(),
            errors=((StateKind.PLAN, "ControlPlaneBoundExceeded: ..."),),
            faults=((StateKind.PLAN, BOUND_LOCK),),
        ),
        duration_s=0.01,
        now=1_000.0,
    )

    series = recorder.snapshot().series()
    assert series[f'amf_rehydration_fault_total{{category="{BOUND_LOCK}"}}'] == 1
    assert series[f'amf_rehydration_fault_total{{category="{FAULT_OUTAGE}"}}'] == 0
    assert series["amf_rehydration_success_total"] == 0


def test_the_exposition_is_scrapeable_text() -> None:
    lab = _service()
    lab.service.round_once()

    body = render(lab.service.metrics())

    lines = [line for line in body.splitlines() if line]
    assert len(lines) == SERIES_COUNT
    assert all(len(line.rsplit(" ", 1)) == 2 for line in lines)
    assert body.endswith("\n")


def test_the_config_is_frozen() -> None:
    """A process whose configuration can be edited mid-run cannot be reasoned about."""
    config = from_env(MINIMAL_ENV)

    with pytest.raises(AttributeError):
        config.dsn = "postgresql://elsewhere/db"  # type: ignore[misc]
    assert isinstance(config, ServiceConfig)


# --- the ok_publish_pending detector (SP2) -------------------------------------------------------


def test_an_unpublished_write_raises_the_pending_age() -> None:
    """R2-04's named alarm: *"alarm when an `ok_publish_pending` write is older than one period"*.

    SP2 is the defect where a write commits to Postgres, the publish fails, and the write is
    therefore invisible to every gateway. That invisibility is why only the control plane can
    detect it, and `StatePublishPending` shipped reading a counter nothing produced.
    """
    lab = _service()
    lab.service.round_once()
    assert lab.service.metrics().series()["amf_state_publish_pending_oldest_seconds"] == 0.0

    # Commit a write whose publish fails: durable in Postgres, absent from the store.
    def publish_fails(*_args: object, **_kwargs: object) -> bool:
        raise ConnectionError("injected store failure")

    published = lab.store.publish_record
    lab.store.publish_record = publish_fails  # type: ignore[method-assign]
    outcome = lab.writer.plan_set("org-pending", {"org_id": "org-pending"})
    lab.store.publish_record = published  # type: ignore[method-assign]

    assert outcome.status == "ok_publish_pending", "the write must be durable but unpublished"

    # A round that cannot publish it (store still broken for the whole kind) must REPORT it.
    lab.store.publish_kind = publish_fails  # type: ignore[method-assign]
    lab.service.round_once()

    pending = lab.service.metrics().series()["amf_state_publish_pending_oldest_seconds"]
    assert pending > 0.0, "a committed, unpublished write must show a non-zero age"


def test_the_pending_age_returns_to_zero_once_a_rehydrator_publishes_it() -> None:
    """The alarm must clear by itself. A detector that latches is a detector nobody trusts."""
    lab = _service()
    lab.service.round_once()

    def publish_fails(*_args: object, **_kwargs: object) -> bool:
        raise ConnectionError("injected store failure")

    published = lab.store.publish_record
    lab.store.publish_record = publish_fails  # type: ignore[method-assign]
    lab.writer.plan_set("org-pending", {"org_id": "org-pending"})
    lab.store.publish_record = published  # type: ignore[method-assign]

    # This round CAN publish, so it carries the pending write itself.
    lab.service.round_once()
    lab.service.round_once()

    assert lab.service.metrics().series()["amf_state_publish_pending_oldest_seconds"] == 0.0


def test_measuring_the_pending_age_costs_nothing_when_nothing_is_pending() -> None:
    """The round is O(1) by GW05c's design and must stay that way.

    An alarm input that queried Postgres on every healthy round would put the freshness budget on
    the wrong side of the trade: the stamp is what the fleet's availability depends on.
    """
    lab = _service()
    calls: list[int] = []
    real = lab.db.oldest_unpublished_at

    def counted(kind: StateKind, above_feed_seq: int) -> float | None:
        calls.append(above_feed_seq)
        return real(kind, above_feed_seq)

    lab.db.oldest_unpublished_at = counted  # type: ignore[method-assign]
    lab.service.round_once()  # first round repairs (store is empty), so it may query
    calls.clear()
    lab.service.round_once()  # now healthy
    lab.service.round_once()

    assert calls == [], "a healthy round must not query the unpublished age at all"


def test_a_failure_to_measure_the_pending_age_never_fails_the_round() -> None:
    """Observability must not be able to withhold a freshness stamp.

    The stamp is the fleet's enforcement signal; losing an alarm input is strictly cheaper than
    losing the stamp, so this input fails soft and says so in the log.
    """
    lab = _service()
    lab.service.round_once()
    lab.store.flush()

    def broken(kind: StateKind, above_feed_seq: int) -> float | None:
        raise RuntimeError("injected measurement failure")

    lab.db.oldest_unpublished_at = broken  # type: ignore[method-assign]
    lab.service.round_once()

    series = lab.service.metrics().series()
    assert series["amf_rehydration_success_total"] == 2, "the round must still have succeeded"
    assert series["amf_state_publish_pending_oldest_seconds"] == 0.0


# --- concurrent-publish safety (the HA clause's other half) ---------------------------------------


def test_a_slower_rehydrators_stale_repair_cannot_regress_a_kind() -> None:
    """R2-04: ">= 2 re-hydrators ... PROVEN SAFE CONCURRENTLY".

    The hazard the HA clause itself introduces. `publish_kind` used to be unconditionally
    unguarded, so with two instances running:

        A snapshots plan at feed_seq N
                                      a writer commits N+1 and publishes it
        B publishes N+1                                     manifest = N+1
        A publishes N  (unguarded)                          manifest = N    <- REGRESS

    Readers refuse a regress by design (C36), so the kind goes unreadable until the next round.
    Bounded, but self-inflicted, and reachable only once two re-hydrators actually run.
    """
    lab = _service()
    lab.service.round_once()

    # The generation a SLOW re-hydrator would still be holding.
    counters, records, engaged = lab.db.snapshot(StateKind.PLAN)
    stale = lab.writer.manifest_for(StateKind.PLAN, counters)

    # A newer generation lands first, exactly as the faster peer would publish it.
    lab.writer.plan_set("org-newer", {"org_id": "org-newer"})
    newer = lab.store.stored_feed_seq(StateKind.PLAN)
    assert newer is not None and newer > stale.feed_seq

    landed = lab.store.publish_kind(StateKind.PLAN, records, stale, engaged)

    assert landed is False, "a stale whole-kind publish must be refused, not applied"
    assert lab.store.stored_feed_seq(StateKind.PLAN) == newer, "the manifest moved backwards"
    assert lab.store.whole_kind_publishes_refused == 1


def test_the_store_ahead_repair_may_still_regress_because_it_has_to() -> None:
    """The one caller that legitimately moves the manifest backwards.

    `store_ahead` means the store holds a `feed_seq` Postgres never issued (a stray backup
    restore, a rogue publisher). `repair` bumps the EPOCH so the republished state is newer by
    version, but `_is_ahead` compares `feed_seq`, which the bump does not lift above the store's.
    Guarding this path would therefore leave the divergence permanent -- so it is the single
    exemption, and it is passed by reason rather than hard-coded.
    """
    lab = _service()
    lab.service.round_once()
    counters, records, engaged = lab.db.snapshot(StateKind.PLAN)
    ahead = KindCounters(
        version=counters.version,
        feed_seq=counters.feed_seq + 9,
        count=counters.count,
        on_count=counters.on_count,
    )
    lab.store.publish_kind(
        StateKind.PLAN,
        records,
        lab.writer.manifest_for(StateKind.PLAN, ahead),
        engaged,
        allow_regress=True,
    )
    assert lab.service._rehydrator.diagnose(StateKind.PLAN) == STORE_AHEAD  # noqa: SLF001

    summary = lab.service.round_once_summary()

    assert [event.reason for event in summary.repairs] == [STORE_AHEAD], (
        "the store_ahead repair must land, which needs the regress exemption"
    )
    assert lab.db.counters(StateKind.PLAN).version.epoch > counters.version.epoch, (
        "the epoch must bump so the republished state is newer than anything already applied"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PRE-EXISTING GW05c/R2-02 defect, NOT R2-04. `repair_epoch` advances the counter's "
        "feed_seq to N+1 but the records keep their original feed_seq <= N, so `_compare` sees "
        "head.index_top < manifest.feed_seq and returns INDEX on every subsequent round -- a "
        "permanent O(records) whole-kind republish once per period. Measured: 8 consecutive "
        "rounds, 8 whole-kind publishes, diagnose never returns to None. Violates GW05c's "
        "'per-record publish, never a whole-kind rewrite'. Fixing it means either re-signing the "
        "records at new positions during an epoch repair or comparing index_top against the "
        "records' own max feed_seq -- a GW05c design decision, not R2-04's to guess at. Remove "
        "this marker when it is fixed."
    ),
)
def test_a_store_ahead_repair_settles_instead_of_looping() -> None:
    """What SHOULD happen after a `store_ahead` repair: the kind reads clean and stays clean."""
    lab = _service()
    lab.service.round_once()
    counters, records, engaged = lab.db.snapshot(StateKind.PLAN)
    ahead = KindCounters(
        version=counters.version,
        feed_seq=counters.feed_seq + 9,
        count=counters.count,
        on_count=counters.on_count,
    )
    lab.store.publish_kind(
        StateKind.PLAN,
        records,
        lab.writer.manifest_for(StateKind.PLAN, ahead),
        engaged,
        allow_regress=True,
    )

    lab.service.round_once()  # heals the divergence
    lab.store.reset_counters()
    lab.service.round_once()  # must now be a no-op
    lab.service.round_once()

    assert lab.store.whole_kind_publishes == 0, (
        "a healed kind must not be republished again; this is an O(1) round"
    )


def test_the_twin_and_the_product_agree_on_the_guard() -> None:
    """The twin used to be MORE permissive than the product, so a regress bug could not
    reproduce in the offline suite -- which is the suite CI runs.

    Both now expose `allow_regress` with the same default, and both refuse by default.
    """
    import inspect

    from state_control.publisher import MemoryStore, StatePublisher
    from state_control.valkey import ValkeyPublisher

    for implementation in (MemoryStore, ValkeyPublisher, StatePublisher):
        parameter = inspect.signature(implementation.publish_kind).parameters["allow_regress"]
        assert parameter.default is False, f"{implementation.__name__} defaults to regressing"
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{implementation.__name__}.allow_regress must be keyword-only: a positional flag "
            "is how a caller regresses a kind by accident"
        )
