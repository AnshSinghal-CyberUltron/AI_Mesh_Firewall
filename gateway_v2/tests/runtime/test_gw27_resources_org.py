"""GW27 Task 2: org-limit capacity derivations (D-13) and contract-free retention bounds."""
from __future__ import annotations

import math
import random

import pytest

from gateway_v2.runtime import resources_org
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.resources_org import (
    derive_org_bounds,
    derive_retention_bounds,
)


def _contract(workers: int | None = None) -> ResourceContract:
    return ResourceContract(
        cpu_quota=4.0, memory_limit=2 * 1024**3, fd_limit=65_536, guard_capacity=None,
        target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024**2,
        worker_override=workers, cpu_source="t", mem_source="t", fd_source="t",
    )


def test_burst_is_exact_at_one_worker_and_spread_at_many() -> None:
    assert derive_org_bounds(_contract(1)).burst_per_worker(5) == 5
    b36 = derive_org_bounds(_contract(36))
    assert b36.burst_per_worker(150) == 11
    assert b36.burst_per_worker(5) == 2


# Feature: gw27-firewall-config-fields, Property 6: burst_worker >= ceil(burst/W), exact at W == 1
# Validates: Requirements 3.1
def test_property_burst_worker_never_below_even_share() -> None:
    seed = 2701
    rng = random.Random(seed)
    for i in range(10_000):
        w = rng.randint(1, 64)
        burst = rng.randint(1, 1000)
        got = derive_org_bounds(_contract(w)).burst_per_worker(burst)
        assert got >= math.ceil(burst / w), (seed, i, w, burst)
        if w == 1:
            assert got == burst, (seed, i, burst)


def test_intervals_and_tolerance_floor() -> None:
    b = derive_org_bounds(_contract(4))
    assert b.request_interval_s(6000) == pytest.approx(60.0 / 1500)
    assert b.token_interval_s(4000) == pytest.approx(60.0 / 1000)
    # A tiny limit still tolerates one maximum-size request (no permanent false 429).
    tol = b.token_tolerance_s(1)
    assert tol >= b.token_interval_s(1) * b.max_single_request_cost
    assert tol >= b.tpm_window_s
    assert b.slots >= 1


def test_zero_or_negative_inputs_fail_closed() -> None:
    b = derive_org_bounds(_contract(1))
    for bad in (0, -1):
        with pytest.raises(CapacityUnavailable):
            b.request_interval_s(bad)
        with pytest.raises(CapacityUnavailable):
            b.burst_per_worker(bad)
        with pytest.raises(CapacityUnavailable):
            b.token_interval_s(bad)


def test_retention_bounds_need_no_contract_and_no_workers() -> None:
    bounds = derive_retention_bounds()
    assert bounds.hard_floor_days == 1
    assert bounds.stamp_max_days == 365
    assert bounds.sweep_batch >= 1 and bounds.org_page >= 1 and bounds.max_batches_per_org >= 1


def test_retention_minimum_check_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resources_org, "_RETENTION_SWEEP_BATCH", 0)
    with pytest.raises(CapacityUnavailable):
        derive_retention_bounds()
