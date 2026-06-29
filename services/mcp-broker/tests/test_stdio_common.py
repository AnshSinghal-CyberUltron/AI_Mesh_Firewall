"""Unit tests for shared stdio MCP denylist and OAuth heuristics."""

from __future__ import annotations

import logging

import pytest

from ai_mesh_shared.mcp_stdio_common import (
    _SECRET_ENV_DENYLIST,
    _build_child_env,
    _looks_like_oauth_prompt,
)


def test_secret_env_denylist_includes_gateway_internal_api_key():
    assert "GATEWAY_INTERNAL_API_KEY" in _SECRET_ENV_DENYLIST


def test_secret_env_denylist_includes_mcp_broker_internal_key():
    assert "MCP_BROKER_INTERNAL_KEY" in _SECRET_ENV_DENYLIST


def test_build_child_env_blocks_mcp_broker_key_in_requested_env(caplog):
    with caplog.at_level(logging.WARNING):
        child = _build_child_env(
            {"MCP_BROKER_INTERNAL_KEY": "must-not-leak", "LINEAR_API_KEY": "byok-ok"},
            "acme",
            host_environ={"PATH": "/usr/bin"},
        )
    assert "MCP_BROKER_INTERNAL_KEY" not in child
    assert child["LINEAR_API_KEY"] == "byok-ok"
    assert any("MCP_BROKER_INTERNAL_KEY" in r.message for r in caplog.records)


def test_build_child_env_blocks_gateway_internal_api_key_in_requested_env(caplog):
    with caplog.at_level(logging.WARNING):
        child = _build_child_env(
            {
                "GATEWAY_INTERNAL_API_KEY": "must-not-leak",
                "LINEAR_API_KEY": "byok-ok",
            },
            "acme",
            host_environ={"PATH": "/usr/bin"},
        )
    assert "GATEWAY_INTERNAL_API_KEY" not in child
    assert child["LINEAR_API_KEY"] == "byok-ok"
    assert child["MCP_REMOTE_CONFIG_DIR"] == "/tmp/mcp-orgs/acme/mcp-auth"
    assert any("GATEWAY_INTERNAL_API_KEY" in r.message for r in caplog.records)


def test_build_child_env_strips_denylisted_vars_from_host_passthrough():
    child = _build_child_env(
        None,
        "acme",
        host_environ={
            "PATH": "/usr/bin",
            "GATEWAY_INTERNAL_API_KEY": "host-secret",
            "PYTHONPATH": "/evil",
        },
    )
    assert child["PATH"] == "/usr/bin"
    assert "GATEWAY_INTERNAL_API_KEY" not in child
    assert "PYTHONPATH" not in child


def test_build_child_env_honors_remote_config_dir_override():
    child = _build_child_env(
        None,
        "acme",
        host_environ={"PATH": "/usr/bin"},
        remote_config_dir="/data/mcp-auth",
    )
    assert child["MCP_REMOTE_CONFIG_DIR"] == "/data/mcp-auth"


@pytest.mark.parametrize(
    ("line", "oauth_header_injected", "expected"),
    [
        (
            "[57] Discovered authorization server: https://mcp.linear.app",
            True,
            False,
        ),
        (
            "Please visit https://example.com/oauth to authorize this app",
            False,
            True,
        ),
    ],
)
def test_oauth_prompt_heuristics(line, oauth_header_injected, expected):
    assert _looks_like_oauth_prompt(line, oauth_header_injected=oauth_header_injected) is expected
