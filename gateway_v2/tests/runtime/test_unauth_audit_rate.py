"""GW35: the unauthenticated-audit rate bound derives from the contract and fails closed."""

from __future__ import annotations

import dataclasses

import pytest

from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.resources_audit import unauth_audit_rate


def _contract(worker_override: int | None) -> ResourceContract:
    return ResourceContract(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        guard_capacity=None,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 * 1024 * 1024,
        worker_override=worker_override,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )


def test_rate_scales_with_workers() -> None:
    assert unauth_audit_rate(_contract(1)) >= 1
    assert unauth_audit_rate(_contract(4)) == 4 * unauth_audit_rate(_contract(1))


def test_underivable_capacity_raises() -> None:
    with pytest.raises(CapacityUnavailable):
        unauth_audit_rate(dataclasses.replace(_contract(1), worker_override=0))
