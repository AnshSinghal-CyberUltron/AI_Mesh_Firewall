"""MCP_EXT_ALLOWED_DOMAINS + MCP_EXT_PROXY_HTTP_HOSTS for validation stubs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

import mcp_proxy  # noqa: E402


def test_ext_allowed_domains_env_extension(monkeypatch):
    monkeypatch.setenv("MCP_EXT_ALLOWED_DOMAINS", "mcp-stub:9999,validation.example")
    mcp_proxy._refresh_allowed_mcp_domains()
    assert "mcp-stub:9999" in mcp_proxy._ALLOWED_MCP_DOMAINS
    assert "validation.example" in mcp_proxy._ALLOWED_MCP_DOMAINS
    assert "mcp.context7.com" in mcp_proxy._ALLOWED_MCP_DOMAINS


def test_ext_proxy_http_hosts_use_http_scheme(monkeypatch):
    monkeypatch.setenv("MCP_EXT_PROXY_HTTP_HOSTS", "mcp-stub:9999")
    url = mcp_proxy._ext_proxy_target_url("mcp-stub:9999", "mcp")
    assert url == "http://mcp-stub:9999/mcp"


def test_ext_proxy_default_https_scheme(monkeypatch):
    monkeypatch.delenv("MCP_EXT_PROXY_HTTP_HOSTS", raising=False)
    url = mcp_proxy._ext_proxy_target_url("mcp.context7.com", "mcp")
    assert url == "https://mcp.context7.com/mcp"
