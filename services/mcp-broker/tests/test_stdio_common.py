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


# P7.25 — Per-org credential/env isolation adversarial tests


def test_cross_org_env_isolation():
    # Org A's BYOK secret must NOT appear in Org B's child env, even if
    # both are built from the same (shared) host environment.
    host = {"PATH": "/usr/bin", "HOME": "/home/sandbox"}
    env_org_a = {"LINEAR_API_KEY": "org-a-secret", "GITHUB_TOKEN": "tok-orgA"}
    env_org_b = {"NOTION_API_KEY": "org-b-secret"}

    child_a = _build_child_env(env_org_a, "org-a", host_environ=host)
    child_b = _build_child_env(env_org_b, "org-b", host_environ=host)

    # Org A's secrets must not appear in Org B's env
    assert "LINEAR_API_KEY" not in child_b
    assert "GITHUB_TOKEN" not in child_b
    # Org B's secrets must not appear in Org A's env
    assert "NOTION_API_KEY" not in child_a
    # Each org gets its own MCP_REMOTE_CONFIG_DIR
    assert child_a["MCP_REMOTE_CONFIG_DIR"] != child_b["MCP_REMOTE_CONFIG_DIR"]
    assert "org-a" in child_a["MCP_REMOTE_CONFIG_DIR"]
    assert "org-b" in child_b["MCP_REMOTE_CONFIG_DIR"]


def test_ld_preload_stripped_from_requested_env():
    # An attacker-controlled MCP server supplying LD_PRELOAD must have it stripped
    # so it cannot hijack shared libraries in the sandbox process.
    child = _build_child_env(
        {"LD_PRELOAD": "/evil/lib.so", "LINEAR_API_KEY": "ok"},
        "acme",
        host_environ={"PATH": "/usr/bin"},
    )
    assert "LD_PRELOAD" not in child
    assert child["LINEAR_API_KEY"] == "ok"


def test_ld_preload_stripped_from_host_env():
    # Host environment LD_PRELOAD must also be blocked — it is not in
    # _SAFE_ENV_PASSTHROUGH so it is excluded from the allowlist path.
    child = _build_child_env(
        None,
        "acme",
        host_environ={"PATH": "/usr/bin", "LD_PRELOAD": "/host/evil.so"},
    )
    assert "LD_PRELOAD" not in child


def test_dyld_insert_libraries_stripped():
    # macOS-style library injection must be blocked in the same way.
    child = _build_child_env(
        {"DYLD_INSERT_LIBRARIES": "/evil/lib.dylib"},
        "acme",
        host_environ={"PATH": "/usr/bin"},
    )
    assert "DYLD_INSERT_LIBRARIES" not in child


def test_all_denylist_vars_absent_in_child():
    # Every variable in the denylist must be stripped even when the caller
    # attempts to pass all of them in the `env` dict simultaneously.
    poison = {k: f"secret-{k}" for k in _SECRET_ENV_DENYLIST}
    child = _build_child_env(poison, "acme", host_environ={"PATH": "/usr/bin"})
    for key in _SECRET_ENV_DENYLIST:
        assert key not in child, f"{key!r} leaked into child env"


def test_all_denylist_vars_absent_even_in_host_env():
    # Denylist vars present in the host environment must not appear in the child.
    host_with_secrets = {k: f"host-{k}" for k in _SECRET_ENV_DENYLIST}
    host_with_secrets["PATH"] = "/usr/bin"
    child = _build_child_env(None, "acme", host_environ=host_with_secrets)
    for key in _SECRET_ENV_DENYLIST:
        assert key not in child, f"host {key!r} leaked into child env"


def test_remote_config_dir_unique_per_org():
    # Two calls with different org_slugs must produce different MCP_REMOTE_CONFIG_DIR
    # values — OAuth token stores for different orgs must not overlap.
    host = {"PATH": "/usr/bin"}
    orgs = ["alpha", "beta", "gamma-corp", "zeroshield"]
    dirs = [
        _build_child_env(None, org, host_environ=host)["MCP_REMOTE_CONFIG_DIR"]
        for org in orgs
    ]
    assert len(set(dirs)) == len(orgs), "MCP_REMOTE_CONFIG_DIR must be unique per org"


def test_build_child_env_cross_org_byok_isolation():
    # item #25: Org A's per-server BYOK credentials must NEVER appear in Org B's
    # child env, and a denylisted broker secret never reaches either child. Each
    # org gets its OWN MCP_REMOTE_CONFIG_DIR so OAuth tokens can't cross orgs.
    host = {"PATH": "/usr/bin"}
    a = _build_child_env(
        {"LINEAR_API_KEY": "lin_A_secret", "MCP_BROKER_INTERNAL_KEY": "broker-secret"},
        "org-a",
        host_environ=host,
    )
    b = _build_child_env({"GITHUB_TOKEN": "ghp_B_secret"}, "org-b", host_environ=host)

    # A's BYOK only in A; B's BYOK only in B — no cross-tenant credential bleed.
    assert a.get("LINEAR_API_KEY") == "lin_A_secret"
    assert "LINEAR_API_KEY" not in b
    assert b.get("GITHUB_TOKEN") == "ghp_B_secret"
    assert "GITHUB_TOKEN" not in a
    # Denylisted broker/infra secret never reaches ANY child.
    assert "MCP_BROKER_INTERNAL_KEY" not in a
    assert "MCP_BROKER_INTERNAL_KEY" not in b
    # Per-org OAuth token dirs are distinct.
    assert a["MCP_REMOTE_CONFIG_DIR"] == "/tmp/mcp-orgs/org-a/mcp-auth"
    assert b["MCP_REMOTE_CONFIG_DIR"] == "/tmp/mcp-orgs/org-b/mcp-auth"
    assert a["MCP_REMOTE_CONFIG_DIR"] != b["MCP_REMOTE_CONFIG_DIR"]


# CHG-0044 (item 8) — npm supply-chain: install lifecycle scripts must be OFF for the
# spawned stdio child. The container sets npm_config_ignore_scripts=true, but the child
# env is rebuilt from _SAFE_ENV_PASSTHROUGH (omits it) and REPLACES the process env, so
# the flag never reached the npx child that actually fetches untrusted packages.


def test_build_child_env_forces_npm_ignore_scripts_by_default():
    # Default (no server env): the child that runs `npx <pkg>` must carry
    # ignore-scripts=true so an untrusted package's postinstall cannot execute on fetch.
    child = _build_child_env(None, "acme", host_environ={"PATH": "/usr/bin"})
    assert child["npm_config_ignore_scripts"] == "true"


def test_build_child_env_ignore_scripts_not_overridable_by_server_spec():
    # A malicious/misconfigured server registration must NOT re-enable npm lifecycle
    # scripts via its own env — the forced pin runs LAST, after the server-spec merge.
    for attempt in ("false", "", "0", "no", "FALSE"):
        child = _build_child_env(
            {"npm_config_ignore_scripts": attempt, "LINEAR_API_KEY": "ok"},
            "acme",
            host_environ={"PATH": "/usr/bin"},
        )
        assert child["npm_config_ignore_scripts"] == "true", (
            f"server-spec env {attempt!r} defeated ignore-scripts"
        )
        assert child["LINEAR_API_KEY"] == "ok"  # legitimate BYOK still passes through


def test_build_child_env_ignore_scripts_not_overridable_by_host_env():
    # Even a host env that (mis)sets ignore-scripts=false is overridden by the
    # forced-true pin for the child that fetches untrusted packages.
    child = _build_child_env(
        None,
        "acme",
        host_environ={"PATH": "/usr/bin", "npm_config_ignore_scripts": "false"},
    )
    assert child["npm_config_ignore_scripts"] == "true"
