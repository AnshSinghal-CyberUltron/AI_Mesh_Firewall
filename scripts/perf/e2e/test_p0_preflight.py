"""Unit tests for P0.0 preflight gates (no Docker)."""
from __future__ import annotations

import pytest

from p0_preflight import (
    FORBIDDEN_HOST_PORTS,
    empty_redis_globs_ok,
    peer_project_ok,
    ram_matches_seed,
    refuse_gateway_url,
    routing_catalog_ok,
)


def test_refuse_host_8300():
    with pytest.raises(ValueError, match="forbidden"):
        refuse_gateway_url("http://127.0.0.1:8300")
    with pytest.raises(ValueError, match="forbidden"):
        refuse_gateway_url("http://localhost:8300/v1")
    refuse_gateway_url("http://127.0.0.1:18300")


def test_forbidden_set_includes_live_ports():
    assert ("127.0.0.1", 8300) in FORBIDDEN_HOST_PORTS
    assert ("127.0.0.1", 8100) in FORBIDDEN_HOST_PORTS


def test_peer_must_be_aimf_p0():
    assert peer_project_ok({"com.docker.compose.project": "aimf_p0"}) is True
    assert peer_project_ok({"com.docker.compose.project": "aimeshperf"}) is False
    assert peer_project_ok({"com.docker.compose.project": "ai_mesh_firewall"}) is False
    assert peer_project_ok({}) is False


def test_ram_empty_while_redis_full_fails():
    ok, reason = ram_matches_seed(
        redis_policy_count=4,
        observability={"organization": "aimfp0", "policy": {"policy_count": 0, "version": None}},
        expected_org="aimfp0",
    )
    assert ok is False
    assert "RAM" in reason or "empty" in reason.lower()


def test_ram_org_mismatch_fails():
    ok, reason = ram_matches_seed(
        redis_policy_count=4,
        observability={"organization": "zeroshield", "policy": {"policy_count": 4, "version": 1}},
        expected_org="aimfp0",
    )
    assert ok is False
    assert "org" in reason.lower() or "organization" in reason.lower()


def test_ram_and_org_match():
    ok, reason = ram_matches_seed(
        redis_policy_count=4,
        observability={"organization": "aimfp0", "policy": {"policy_count": 4, "version": 12}},
        expected_org="aimfp0",
    )
    assert ok is True
    assert reason == ""


def test_empty_redis_glob():
    assert empty_redis_globs_ok([]) is True
    assert empty_redis_globs_ok(["policies:compiled:aimfp0"]) is False
    assert empty_redis_globs_ok(["firewall:config"]) is False
    assert empty_redis_globs_ok(["kill_switch:aimfp0:global"]) is False
    assert empty_redis_globs_ok(["llm:model_configs:x"]) is False


def test_routing_catalog_empty_fails():
    ok, reason = routing_catalog_ok({"models": [], "routing": []})
    assert ok is False
    assert "empty" in reason.lower()


def test_routing_catalog_internal_only_fails():
    ok, reason = routing_catalog_ok(
        {"routing": [{"model_name": "zeroshield-model", "provider": "internal", "api_key_set": False}]}
    )
    assert ok is False
    assert "credentialed" in reason.lower()


def test_routing_catalog_byok_ok():
    ok, reason = routing_catalog_ok(
        {"routing": [{"model_name": "gpt-4o-mini", "provider": "openai", "api_key_set": True}]}
    )
    assert ok is True
    assert reason == ""
