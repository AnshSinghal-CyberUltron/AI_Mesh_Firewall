# Validates: Requirements 8 (resource derivations: every incident accessor is a contract derivation)
"""GW29 incident accessors: values come from the contract constants, fail closed, snapshot keys."""

from __future__ import annotations

import math
import types

import pytest

import gateway_v2.runtime.resources as resources
import gateway_v2.runtime.resources_incident as incident_bounds
from gateway_v2.domain.incident import ModelOutcome
from gateway_v2.domain.posture import MIN_RETRY_AFTER_S
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.resources_snapshot import snapshot


def _contract(*, fd_limit: int = 4096, target_p99_ms: float = 20.0) -> ResourceContract:
    rss = 100 * 1024 * 1024
    return from_signals(
        HardwareSignals(
            cpu_quota=4.0, memory_limit=rss * 64, fd_limit=fd_limit,
            cpu_source="test", mem_source="test", fd_source="test",
        ),
        target_p99_ms=target_p99_ms, utilization_cap=0.75, per_worker_rss=rss,
    )


def test_every_accessor_derives_from_the_contract_constants() -> None:
    c = _contract()
    assert c.risk_window_s() == resources._RISK_WINDOW_S
    assert c.risk_buckets() == resources._RISK_BUCKETS
    assert c.risk_bucket_s() == resources._RISK_WINDOW_S / resources._RISK_BUCKETS
    assert c.risk_min_samples() == resources._RISK_MIN_SAMPLES
    assert c.risk_probation_min_samples() == resources._RISK_PROBATION_MIN
    assert c.risk_threshold() == resources._RISK_THRESHOLD
    assert c.risk_min_distinct_orgs() == resources._RISK_MIN_ORGS
    assert c.risk_max_models() == c.metrics_directory_slots() // resources._RISK_MODELS_DIV
    assert c.local_isolation_slots() == c.risk_max_models()
    assert c.auto_isolation_duration_s() == c.risk_window_s() * resources._AUTO_ISOLATE_WINDOWS
    assert c.max_isolation_hold_s() == c.auto_isolation_duration_s() * resources._HOLD_CAP_MULT
    assert c.revocation_ledger_slots() == c.connection_budget() * resources._REVOKE_SLOTS_MULT
    assert c.incident_event_queue_slots() == c.event_log_rate() * resources._EVENT_QUEUE_SLOTS_MULT
    assert c.incident_event_window_s() == c.metrics_window_s()
    p99_s = c.target_p99_ms / resources._MS_PER_S
    assert c.notifier_timeout_s() == max(p99_s * resources._NOTIFIER_TIMEOUT_MULT, p99_s)
    assert c.incident_retry_jitter_s() == MIN_RETRY_AFTER_S * resources._RETRY_JITTER_FRACTION


def test_bucket_width_times_buckets_is_the_window() -> None:
    c = _contract()
    assert math.isclose(c.risk_bucket_s() * c.risk_buckets(), c.risk_window_s())


def test_weights_are_a_read_only_unit_interval_map_over_every_outcome() -> None:
    w = _contract().risk_weights()
    assert isinstance(w, types.MappingProxyType) and set(w) == set(ModelOutcome)
    assert all(0.0 <= v <= 1.0 for v in w.values())
    assert w[ModelOutcome.OK] == 0.0
    hard = (ModelOutcome.SERVER_ERROR, ModelOutcome.TIMEOUT, ModelOutcome.MALFORMED)
    assert all(w[o] == 1.0 for o in hard)
    assert w[ModelOutcome.RATE_LIMITED] == w[ModelOutcome.GUARD_HIT] == resources._RISK_WEIGHT_SOFT
    with pytest.raises(TypeError):
        w[ModelOutcome.OK] = 1.0  # type: ignore[index]


def test_threshold_pins_the_documented_429_cases() -> None:
    c = _contract()
    soft = c.risk_weights()[ModelOutcome.RATE_LIMITED]
    assert 0.5 * soft < c.risk_threshold() <= 1.0 * soft  # 50% 429 no trip, 100% 429 trips
    assert 0.5 >= c.risk_threshold() > 0.1  # 50% hard trips, 10% hard does not


def test_snapshot_contains_the_incident_keys() -> None:
    c = _contract()
    snap = snapshot(c, ())
    for key in c.incident_snapshot():
        assert key in snap
    assert snap["risk_window_s"] == c.risk_window_s()
    assert snap["auto_isolation_duration_s"] == c.auto_isolation_duration_s()
    assert snap["workers"] == c.workers()  # existing keys intact


def test_metrics_bound_below_one_fails_closed() -> None:
    tiny = _contract(fd_limit=1)  # connection_budget small enough that a share rounds to zero
    try:
        tiny.risk_max_models()
    except CapacityUnavailable:
        return
    assert tiny.risk_max_models() >= 1


@pytest.mark.parametrize(
    ("name", "bad"),
    [
        ("_RISK_THRESHOLD", 1.0),
        ("_RISK_THRESHOLD", 0.0),
        ("_RISK_PROBATION_MIN", resources._RISK_MIN_SAMPLES + 1),
        ("_RISK_BUCKETS", 0),
        ("_AUTO_ISOLATE_WINDOWS", 0),
        ("_RISK_WEIGHT_SOFT", 1.5),
    ],
)
def test_ordering_check_raises_on_a_violated_relation(
    monkeypatch: pytest.MonkeyPatch, name: str, bad: float,
) -> None:
    incident_bounds.check_incident_ordering()  # the shipped constants are ordered
    monkeypatch.setattr(resources, name, bad)
    with pytest.raises(CapacityUnavailable):
        incident_bounds.check_incident_ordering()


def test_accessors_fail_closed_on_an_unusable_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _contract()
    for name in ("_RISK_WINDOW_S", "_NOTIFIER_TIMEOUT_MULT", "_RETRY_JITTER_FRACTION"):
        monkeypatch.setattr(resources, name, 0.0)
    with pytest.raises(CapacityUnavailable):
        c.risk_window_s()
    with pytest.raises(CapacityUnavailable):
        c.incident_retry_jitter_s()
    monkeypatch.setattr(resources, "_NOTIFIER_TIMEOUT_MULT", -1.0)
    assert c.notifier_timeout_s() == c.target_p99_ms / resources._MS_PER_S  # max() floor
