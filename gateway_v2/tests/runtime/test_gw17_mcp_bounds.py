"""GW17 MCP bounds on the resource contract.

Validates: Requirements 3.1, 6.7, 7.2, 9.1, 9.2, 9.3
"""

from __future__ import annotations

import dataclasses

import pytest

from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import McpBounds, ResourceContract, from_signals


def _contract(per_worker_rss: int = 400 * 1024 * 1024) -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=8 * 1024 * 1024 * 1024,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=per_worker_rss
    )


def test_v1_parity_values() -> None:
    bounds = _contract().mcp_bounds()
    assert bounds.argument_bytes == 512 * 1024
    assert bounds.json_depth == 200
    assert bounds.argument_nodes == 50_000
    assert bounds.result_bytes == 10 * 1024 * 1024
    assert bounds.result_nodes == 1_000_000
    assert bounds.result_blocks == 10_000
    assert (bounds.broker_init_timeout_s, bounds.broker_method_timeout_s) == (120.0, 60.0)
    assert bounds.broker_retry_max == 5
    assert (bounds.broker_backoff_base_s, bounds.broker_backoff_cap_s) == (0.5, 8.0)


def test_body_bound_is_never_looser_than_chat() -> None:
    contract = _contract()
    bounds = contract.mcp_bounds()
    assert bounds.body_bytes == min(McpBounds().body_bytes, contract.max_request_bytes())
    assert bounds.body_bytes <= contract.max_request_bytes()


def test_non_positive_bound_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = dataclasses.replace(McpBounds(), broker_retry_max=0)
    monkeypatch.setattr("gateway_v2.runtime.resources.McpBounds", lambda: broken)
    with pytest.raises(CapacityUnavailable):
        _contract().mcp_bounds()
