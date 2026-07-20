"""Unit tests for shared stdio MCP denylist and OAuth heuristics."""

from __future__ import annotations

import logging

import pytest

from ai_mesh_shared.mcp_stdio_common import (
    _SECRET_ENV_DENYLIST,
    _build_child_env,
    _looks_like_oauth_prompt,
    _redact_url_creds,
    _safe_args_for_log,
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


# ---------------------------------------------------------------------------
# CHG-0107: _safe_args_for_log + _redact_url_creds — mask secret-flag values
# AND URL-embedded credentials before logging stdio spawn args.
# ---------------------------------------------------------------------------


class TestSafeArgsForLog:
    def test_masks_value_after_secret_flag(self):
        assert _safe_args_for_log(["--token", "s3cr3t", "-y", "@pkg"]) == [
            "--token", "***", "-y", "@pkg",
        ]

    def test_masks_inline_secret_flag(self):
        assert _safe_args_for_log(["--api-key=s3cr3t"]) == ["--api-key=***"]

    def test_masks_url_userinfo_password(self):
        # postgres://user:pass@host — whole userinfo masked (CHG-0053 follow-up).
        out = _safe_args_for_log(["-y", "mcp-remote",
                                  "postgres://admin:S3cr3tPass@db.internal:5432/prod"])
        assert out == ["-y", "mcp-remote", "postgres://***@db.internal:5432/prod"]
        assert "S3cr3tPass" not in " ".join(out)

    def test_masks_url_userinfo_token_in_either_position(self):
        tok = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        # token in password position
        out1 = _safe_args_for_log([f"https://x-access-token:{tok}@github.com/o/r"])
        # token in username position (no colon)
        out2 = _safe_args_for_log([f"https://{tok}@github.com/o/r"])
        assert tok not in " ".join(out1) and out1 == ["https://***@github.com/o/r"]
        assert tok not in " ".join(out2) and out2 == ["https://***@github.com/o/r"]

    def test_masks_secret_query_params_only(self):
        out = _safe_args_for_log(
            ["https://api.example.com/mcp?api_key=AKIAIOSFODNN7EXAMPLE&token=abc&page=2"])
        assert out == [
            "https://api.example.com/mcp?api_key=***&token=***&page=2",
        ]  # page=2 (non-secret) preserved

    def test_masks_url_creds_in_non_secret_flag_inline_value(self):
        # --dsn is not a secret flag, but its URL value carries a password.
        assert _safe_args_for_log(["--dsn=postgres://u:pw_SEKRET@h/db"]) == [
            "--dsn=postgres://***@h/db",
        ]

    def test_benign_url_and_pkgspec_untouched(self):
        args = ["-y", "@modelcontextprotocol/server-filesystem@1.0.0",
                "https://safe.example.com/mcp?page=2"]
        assert _safe_args_for_log(args) == args

    def test_bare_positional_without_url_untouched(self):
        assert _safe_args_for_log(["mcp-remote", "tokenish-pkgname"]) == [
            "mcp-remote", "tokenish-pkgname",
        ]


class TestRedactUrlCreds:
    def test_non_url_unchanged(self):
        assert _redact_url_creds("mcp-remote") == "mcp-remote"
        assert _redact_url_creds("--token") == "--token"

    def test_mailto_and_schemeless_unchanged(self):
        # mailto: has no // netloc → not a hierarchical URL, left as-is.
        assert _redact_url_creds("mailto:bob@corp.example") == "mailto:bob@corp.example"

    def test_no_creds_unchanged(self):
        assert _redact_url_creds("https://safe.example.com/x?a=1") == \
            "https://safe.example.com/x?a=1"

    def test_never_raises_on_garbage(self):
        # Fail-safe: any parse hiccup returns the input unchanged, never raises.
        for junk in ["://", "http://[bad", "ht!tp://x", "://@@@"]:
            assert isinstance(_redact_url_creds(junk), str)
