"""GW32 MCP capacity values: derivations, fail-closed bounds, key layout."""

from __future__ import annotations

import dataclasses

import pytest

from gateway_v2.domain.locks import FRESH_MS
from gateway_v2.runtime import resources_mcp as rm
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.store_keys_mcp import McpStoreKeys


def _contract(fd_limit: int = 4096) -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=8 * 1024 * 1024 * 1024,
        fd_limit=fd_limit,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


def test_policy_bounds_are_contract_free() -> None:
    bounds = rm.mcp_policy_bounds()
    assert bounds.tools_max == rm.MCP_TOOLS_MAX
    assert bounds.tool_name_max > 0 and bounds.cap_max > 0


def test_transport_bounds_follow_connection_budget() -> None:
    contract = _contract()
    bounds = rm.derive_transport_bounds(contract)
    assert bounds.ws_global == int(contract.connection_budget() * rm.WS_GLOBAL_FRACTION)
    assert bounds.ws_per_key <= bounds.ws_global
    assert bounds.ws_frame_bytes == contract.mcp_bounds().body_bytes
    assert bounds.stream_recheck_s * 1000 <= FRESH_MS / 2
    assert bounds.token_cache_ttl_s * 1000 <= FRESH_MS


def test_transport_bounds_fail_closed_without_connections() -> None:
    with pytest.raises(CapacityUnavailable):
        rm.derive_transport_bounds(_contract(fd_limit=2))


def test_credential_bounds_share_one_min_len() -> None:
    bounds = rm.derive_credential_bounds(_contract())
    assert bounds.credential_min_len == rm.CREDENTIAL_MIN_LEN == 8
    assert bounds.credential_cache_ttl_s == _contract().mcp_bounds().registry_ttl_s


def test_cap_bounds_are_frozen_and_derived() -> None:
    bounds = rm.derive_cap_bounds(_contract())
    assert bounds.workers >= 1 and bounds.table_slots >= 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        bounds.bootstrap = 5  # type: ignore[misc]


def test_store_keys_share_hash_tag_and_reject_empty() -> None:
    keys = McpStoreKeys()
    for built in (
        keys.session("a"),
        keys.sessions_by_key("k"),
        keys.sessions_by_org("o"),
        keys.sessions_global(),
        keys.cap_used("k"),
        keys.refresh_lock("o", "s"),
        keys.oauth_client("c"),
        keys.oauth_code("h"),
        keys.oauth_access("h"),
        keys.oauth_refresh("h"),
        keys.oauth_family("f"),
        keys.oauth_form("h"),
        keys.oauth_failures("h"),
        keys.oauth_clients_index(),
        keys.oauth_clients_by_source("h"),
    ):
        assert built.startswith("{rv2}:")
    with pytest.raises(ValueError):
        keys.cap_used("")
    with pytest.raises(ValueError):
        keys.refresh_lock("o", "")
