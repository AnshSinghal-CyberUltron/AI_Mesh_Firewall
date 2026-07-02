"""P4.13/P6.18 §3 — remote transports (streamable-http/sse) route through the
per-org sandbox via broker_send_rpc, not a direct gateway httpx dial."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ai_mesh_gateway import mcp_proxy


def test_is_sandbox_routed_stdio_ws_always():
    assert mcp_proxy._is_sandbox_routed("stdio") is True
    assert mcp_proxy._is_sandbox_routed("websocket") is True
    assert mcp_proxy._is_sandbox_routed("bogus") is False


def test_is_sandbox_routed_http_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_HTTP_VIA_SANDBOX", "0")
    assert mcp_proxy._is_sandbox_routed("streamable-http") is False
    assert mcp_proxy._is_sandbox_routed("sse") is False
    monkeypatch.setenv("MCP_HTTP_VIA_SANDBOX", "1")
    assert mcp_proxy._is_sandbox_routed("streamable-http") is True
    assert mcp_proxy._is_sandbox_routed("sse") is True


@pytest.mark.asyncio
async def test_adapter_forward_streamable_http_uses_broker_send_rpc(monkeypatch: pytest.MonkeyPatch):
    # When routed, _adapter_forward must call broker_send_rpc (sandbox path) with the
    # upstream url + injected OAuth token — NOT dial the upstream directly.
    monkeypatch.setenv("MCP_HTTP_VIA_SANDBOX", "1")
    server_config = {
        "url": "https://mcp.example.com/mcp",
        "allowed_hosts": ["mcp.example.com"],
    }
    body = {"method": "tools/call", "params": {"name": "echo", "arguments": {"message": "hi"}}}

    broker_rpc = AsyncMock(return_value={
        "jsonrpc": "2.0", "id": 7,
        "result": {"content": [{"type": "text", "text": "Echo: hi"}]},
    })
    token_getter = AsyncMock(return_value="tok-xyz")

    with patch("mcp_sandbox_client.broker_send_rpc", broker_rpc), \
         patch("mcp_oauth_proxy.get_stored_token", token_getter):
        resp = await mcp_proxy._adapter_forward(
            "streamable-http", server_config, "org-a", "remote-http", body, "2.0", 7,
        )

    assert resp.status_code == 200
    broker_rpc.assert_awaited_once()
    args, kwargs = broker_rpc.call_args
    # org, up_config, method, params positional; oauth_token kwarg
    assert args[0] == "org-a"
    up_config = args[1]
    assert up_config["transport"] == "streamable-http"
    assert up_config["url"] == "https://mcp.example.com/mcp"
    assert up_config["allowed_hosts"] == ["mcp.example.com"]
    assert args[2] == "tools/call"
    assert kwargs["oauth_token"] == "tok-xyz"


@pytest.mark.asyncio
async def test_adapter_forward_sse_missing_url_errors_without_direct_dial(monkeypatch: pytest.MonkeyPatch):
    # A misconfigured remote server (no url) surfaces a JSON-RPC error — and still
    # never dials an upstream directly (broker_send_rpc raises on missing url).
    monkeypatch.setenv("MCP_HTTP_VIA_SANDBOX", "1")
    with patch("mcp_oauth_proxy.get_stored_token", AsyncMock(return_value=None)):
        resp = await mcp_proxy._adapter_forward(
            "sse", {"url": ""}, "org-a", "bad", {"method": "tools/list", "params": {}}, "2.0", 1,
        )
    assert resp.status_code == 200  # graceful JSON-RPC error, not a crash
    import json
    payload = json.loads(resp.body.decode())
    assert payload.get("error") is not None
