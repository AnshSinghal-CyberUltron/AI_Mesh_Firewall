"""Actionable DNS failure message for unprovisioned .everything.stub hosts."""

from __future__ import annotations

import pytest

from upstream_manager import UpstreamError, _assert_upstream_not_ssrf


@pytest.mark.asyncio
async def test_everything_stub_dns_failure_is_actionable(monkeypatch):
    async def _fail_dns(host, port):
        raise OSError("Name or service not known")

    monkeypatch.setattr(
        "upstream_manager.asyncio.get_running_loop",
        lambda: type("L", (), {"getaddrinfo": _fail_dns})(),
    )
    with pytest.raises(UpstreamError) as exc:
        await _assert_upstream_not_ssrf("http-everything.stub")
    assert exc.value.code == -32002
    assert "stub_not_provisioned" in exc.value.message
    assert "transport-stubs" in exc.value.message


@pytest.mark.asyncio
async def test_everything_stub_private_ip_allowed(monkeypatch):
    """Stubs resolve to RFC1918 on the org sandbox network — must not SSRF-block."""

    async def _resolve_private(host, port):
        return [(2, 1, 6, "", ("172.20.0.6", 0))]

    monkeypatch.delenv("MCP_AGENT_ALLOW_INTERNAL_HOSTS", raising=False)
    monkeypatch.setattr(
        "upstream_manager.asyncio.get_running_loop",
        lambda: type("L", (), {"getaddrinfo": _resolve_private})(),
    )
    await _assert_upstream_not_ssrf("http-everything.stub")
