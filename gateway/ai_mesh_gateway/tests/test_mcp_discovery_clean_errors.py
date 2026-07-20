"""Item 02 — the DISCOVERY path (internal_discover_tools) returns CLEAN, non-revealing
errors: a 405 HTML error page → 'HTTP 405 — check the endpoint URL' (never the raw HTML),
a connection failure → a branded transport message (never the raw exc / internal host).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402


def _ok(json_body):
    r = AsyncMock()
    r.status_code = 200
    r.json = lambda: json_body
    r.text = json.dumps(json_body)
    r.headers = {"content-type": "application/json"}
    return r


def _html_error(status=405):
    r = AsyncMock()
    r.status_code = status

    def _raise():
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    r.json = _raise
    r.text = ("<html><head><title>405 Not Allowed</title></head>"
              "<body><center>nginx/1.25.3</center></body></html>")
    r.headers = {"content-type": "text/html"}
    return r


def _req():
    return SimpleNamespace(
        headers={"X-Gateway-Internal-Key": "ok"},
        json=AsyncMock(return_value={"org_slug": "demo", "server_slug": "srv"}),
    )


def _seq_client(responses):
    seq = list(responses)

    async def _post(*_a, **_k):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    c = AsyncMock()
    c.post = AsyncMock(side_effect=_post)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


def _raising_client(exc, after=2):
    n = {"c": 0}

    async def _post(*_a, **_k):
        n["c"] += 1
        if n["c"] > after:
            raise exc
        return _ok({"jsonrpc": "2.0", "id": 1, "result": {}})

    c = AsyncMock()
    c.post = AsyncMock(side_effect=_post)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


async def _drive(client):
    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_is_sandbox_routed", return_value=False),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "streamable-http",
                                             "url": "https://safe.example.com/mcp"})),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "is_safe_outbound_url", return_value=(True, "")),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        resp = await mcp_proxy.internal_discover_tools(_req())
    return json.loads(resp.body.decode("utf-8"))


@pytest.mark.asyncio
async def test_405_html_error_page_returns_clean_message():
    body = await _drive(_seq_client([
        _ok({"jsonrpc": "2.0", "id": 1, "result": {}}),  # initialize
        _ok({}),                                          # notifications/initialized
        _html_error(405),                                 # tools/list → 405 HTML
    ]))
    msg = body["error"]["message"]
    assert "HTTP 405" in msg and "check the endpoint" in msg.lower()
    assert body.get("code") == "MCP_UPSTREAM_HTTP_ERROR"
    assert len(body.get("ref", "")) == 12
    # NEVER the raw HTML / server banner
    blob = json.dumps(body)
    for tok in ("<html>", "nginx", "Not Allowed", "<center>"):
        assert tok not in blob, f"leak: {tok!r}"


@pytest.mark.asyncio
async def test_dns_failure_returns_clean_message_no_host():
    exc = httpx.ConnectError(
        "[Errno -2] Name or service not known: secret-host.internal.corp:8931")
    body = await _drive(_raising_client(exc))
    msg = body["error"]["message"]
    assert "could not reach" in msg.lower()
    assert body.get("code") == "MCP_DNS_FAILURE"
    blob = json.dumps(body)
    for tok in ("secret-host", "internal.corp", "Errno", "getaddrinfo", "Name or service"):
        assert tok not in blob, f"leak: {tok!r}"


@pytest.mark.asyncio
async def test_connection_refused_returns_clean_message():
    exc = httpx.ConnectError("[Errno 111] Connection refused: 10.1.2.3:9931")
    body = await _drive(_raising_client(exc))
    assert body.get("code") == "MCP_CONNECTION_REFUSED"
    blob = json.dumps(body)
    assert "10.1.2.3" not in blob and "Errno" not in blob


@pytest.mark.asyncio
async def test_ssrf_reject_dns_reason_is_clean():
    # CLEANUP-06: the SSRF guard rejects before any fetch; its reason can carry a raw
    # DNS errno or a resolved internal IP — the client must see only a clean message.
    ssrf_reason = ("DNS resolution failed for 'secret-host.internal': "
                   "[Errno -2] Name or service not known")
    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_is_sandbox_routed", return_value=False),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "streamable-http",
                                             "url": "https://secret-host.internal/mcp"})),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "is_safe_outbound_url", return_value=(False, ssrf_reason)),
    ):
        resp = await mcp_proxy.internal_discover_tools(_req())
    body = json.loads(resp.body.decode("utf-8"))
    assert body.get("code") == "MCP_DNS_FAILURE"
    blob = json.dumps(body)
    for tok in ("secret-host.internal", "Errno", "Name or service", "SSRF"):
        assert tok not in blob, f"leak: {tok!r}"
