"""Unit tests for shared MCP host-tools contract."""

from __future__ import annotations

import os

import pytest

from ai_mesh_shared.mcp_host_tools import (
    HOST_TOOL_INSTALL_FAILED_PREFIX,
    HostToolValidationError,
    format_host_tools_spec,
    host_tool_install_cmd,
    merge_host_tools_into_env,
    parse_host_tools_allowlist,
    parse_host_tools_spec,
    validate_host_tools_spec,
)


def test_parse_bare_and_prefixed_entries():
    assert parse_host_tools_spec("semgrep pip:foo npm:bar") == [
        ("pip", "semgrep"),
        ("pip", "foo"),
        ("npm", "bar"),
    ]


def test_parse_comma_separated():
    assert parse_host_tools_spec("pip:semgrep,npm:eslint") == [
        ("pip", "semgrep"),
        ("npm", "eslint"),
    ]


def test_parse_dedupes():
    assert parse_host_tools_spec("semgrep semgrep pip:semgrep") == [("pip", "semgrep")]


def test_reject_invalid_manager():
    with pytest.raises(HostToolValidationError, match="manager"):
        parse_host_tools_spec("gem:semgrep")


def test_reject_invalid_package_injection():
    with pytest.raises(HostToolValidationError, match="package"):
        parse_host_tools_spec("pip:evil;rm -rf /")


def test_format_host_tools_spec_roundtrip():
    spec = format_host_tools_spec(["semgrep", "npm:eslint", {"manager": "uv", "package": "ruff"}])
    assert parse_host_tools_spec(spec) == [
        ("pip", "semgrep"),
        ("npm", "eslint"),
        ("uv", "ruff"),
    ]


def test_augment_path_for_host_tools_prepends_writable_bins(tmp_path, monkeypatch):
    from ai_mesh_shared.mcp_stdio_common import augment_path_for_host_tools

    uv_bin = tmp_path / "uvbin"
    uv_bin.mkdir()
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(uv_bin))
    child = {"PATH": "/usr/bin"}
    augment_path_for_host_tools(child)
    assert child["PATH"].startswith(str(uv_bin))
    assert "/usr/bin" in child["PATH"]


def test_merge_manual_env_wins_over_catalog():
    merged = merge_host_tools_into_env(
        {"MCP_HOST_TOOLS": "pip:manual"},
        ["pip:catalog"],
    )
    assert merged["MCP_HOST_TOOLS"] == "pip:manual"


def test_merge_catalog_when_no_manual():
    merged = merge_host_tools_into_env({}, ["pip:semgrep"])
    assert merged["MCP_HOST_TOOLS"] == "semgrep"


def test_host_tool_install_cmd_pip_and_npm():
    assert host_tool_install_cmd("pip", "semgrep") == [
        "uv", "tool", "install", "--quiet", "semgrep",
    ]
    assert host_tool_install_cmd("npm", "eslint") == [
        "npm", "install", "-g", "--no-audit", "--no-fund", "eslint",
    ]


def test_allowlist_blocks_undeclared(monkeypatch):
    monkeypatch.setenv("MCP_HOST_TOOLS_ALLOWLIST", "semgrep,eslint")
    from ai_mesh_shared.mcp_host_tools import assert_host_tool_allowed

    assert_host_tool_allowed("semgrep")
    with pytest.raises(HostToolValidationError, match="allowlist"):
        assert_host_tool_allowed("cowsay")


def test_allowlist_empty_allows_any(monkeypatch):
    monkeypatch.delenv("MCP_HOST_TOOLS_ALLOWLIST", raising=False)
    from ai_mesh_shared.mcp_host_tools import assert_host_tool_allowed

    assert_host_tool_allowed("anything-valid1")


def test_validate_empty_spec_ok():
    validate_host_tools_spec("")
    validate_host_tools_spec("   ")


def test_install_failed_prefix_stable():
    assert HOST_TOOL_INSTALL_FAILED_PREFIX == "host tool install failed:"
