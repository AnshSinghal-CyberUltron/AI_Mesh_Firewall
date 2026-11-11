"""GW28 routing / stall-bound capacity accessors (design C8, P7 + P11 derivation clauses)."""

from __future__ import annotations

import random

import pytest

import gateway_v2.runtime.resources as resources
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.kinds import HardwareSignals, PoolKind
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.resources_snapshot import snapshot


def _contract(target_p99_ms: float = 20.0, cpu: float = 4.0) -> ResourceContract:
    rss = 400 * 1024 * 1024
    signals = HardwareSignals(
        cpu_quota=cpu,
        memory_limit=rss * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=target_p99_ms, utilization_cap=0.75, per_worker_rss=rss
    )


def test_default_values() -> None:
    c = _contract()
    assert c.upstream_first_event_timeout_s() == pytest.approx(30.0)
    assert c.upstream_gap_timeout_s() == pytest.approx(0.8)
    assert c.upstream_total_timeout_s() == pytest.approx(c.max_stream_duration_s() - 0.06)
    assert c.routing_max_attempts() == resources._ROUTING_MAX_ATTEMPTS
    assert c.routing_max_candidates() == resources._ROUTING_MAX_CANDIDATES
    assert c.routing_max_weight() == resources._ROUTING_MAX_WEIGHT
    assert c.routing_max_identifier_len() == resources._ROUTING_MAX_IDENT_LEN
    assert c.routing_pre_content_budget_s(2) == pytest.approx(60.18)


# Feature: gw28-multi-model-routing, Property 7: derived bounds ordered below the 300 s default
def test_p7_derivation_ordering_over_random_slo() -> None:
    rng = random.Random(0x2801)
    for _ in range(500):
        c = _contract(target_p99_ms=rng.uniform(1.0, 100.0))
        gap, first, total = (
            c.upstream_gap_timeout_s(),
            c.upstream_first_event_timeout_s(),
            c.upstream_total_timeout_s(),
        )
        assert 0 < gap < first < total < c.max_stream_duration_s()
        assert total <= c.max_stream_duration_s() - c.cancellation_bound_s() + 1e-9


# Feature: gw28-multi-model-routing, Property 11: router timers pre-empt the edge timers
def test_p11_timer_precedence_arithmetic() -> None:
    rng = random.Random(0x2811)
    for _ in range(500):
        c = _contract(target_p99_ms=rng.uniform(1.0, 100.0))
        assert c.upstream_gap_timeout_s() < c.inter_chunk_timeout_s() or (
            c.upstream_gap_timeout_s() == c.target_p99_ms / 1000.0
        )
        for n in range(1, c.routing_max_attempts() + 1):
            need = n * (c.upstream_first_event_timeout_s() + c.cancellation_bound_s())
            assert c.routing_pre_content_budget_s(n) > need


def test_pre_content_budget_rejects_non_positive_n() -> None:
    with pytest.raises(CapacityUnavailable):
        _contract().routing_pre_content_budget_s(0)


def test_provider_pool_share() -> None:
    c = _contract()
    pool = c.pool_size(PoolKind.PROVIDER)
    assert c.provider_pool_share(1) == pool
    for n in range(1, pool + 1):
        assert n * c.provider_pool_share(n) <= pool
    with pytest.raises(CapacityUnavailable):
        c.provider_pool_share(pool + 1)
    with pytest.raises(CapacityUnavailable):
        c.provider_pool_share(0)


def test_snapshot_carries_the_four_bounds() -> None:
    c = _contract()
    snap = snapshot(c, ())
    for key in (
        "upstream_first_event_timeout_s",
        "upstream_gap_timeout_s",
        "upstream_total_timeout_s",
        "routing_pre_content_budget_s",
    ):
        assert isinstance(snap[key], float) and snap[key] > 0
