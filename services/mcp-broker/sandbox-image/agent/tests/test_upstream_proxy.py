"""Agent-side unit tests for HTTP/SSE upstream proxy (P4.9)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

BROKER_ROOT = Path(__file__).resolve().parents[3]
SANDBOX_IMAGE = BROKER_ROOT / "sandbox-image"
STDIO_STUB = BROKER_ROOT / "tests" / "fixtures" / "stdio_mcp_stub.py"


def _load_agent_app(monkeypatch):
    monkeypatch.setenv("ORG_SLUG", "test-org")
    sandbox_image = str(SANDBOX_IMAGE)
    if sandbox_image not in sys.path:
        sys.path.insert(0, sandbox_image)
    for mod in (
        "agent.main",
        "agent.stdio_manager",
        "agent.upstream_manager",
        "agent",
    ):
        sys.modules.pop(mod, None)
    return importlib.import_module("agent.main").app


@pytest.fixture
def agent_client(monkeypatch):
    app = _load_agent_app(monkeypatch)
    with TestClient(app) as client:
        yield client


def _http_payload(**overrides):
    base = {
        "server_slug": "http-server",
        "transport": "streamable-http",
        "method": "tools/list",
        "jsonrpc_id": 1,
        "upstream": {
            "url": "https://mcp.example.com/mcp",
            "allowed_hosts": ["mcp.example.com"],
            "headers": {"Authorization": "Bearer test-token"},
            "oauth_client_role": "forbidden_in_sandbox",
        },
    }
    base.update(overrides)
    return base


def test_egress_denied_for_host_not_in_allowlist(agent_client):
    resp = agent_client.post(
        "/rpc",
        json=_http_payload(
            upstream={
                "url": "https://evil.example.com/mcp",
                "allowed_hosts": ["mcp.example.com"],
                "headers": {},
            }
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32002
    assert "egress denied" in body["error"]["message"]


def test_oauth_client_role_rejected(agent_client):
    resp = agent_client.post(
        "/rpc",
        json=_http_payload(
            upstream={
                "url": "https://mcp.example.com/mcp",
                "allowed_hosts": ["mcp.example.com"],
                "oauth_client_role": "client",
            }
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32602


def test_streamable_http_tools_list(agent_client):
    mock_response = httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": [{"name": "echo"}]},
        },
        headers={"Mcp-Session-Id": "sess-abc"},
    )

    async def fake_post(*_args, **_kwargs):
        return mock_response

    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_post)):
        resp = agent_client.post("/rpc", json=_http_payload())

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["tools"][0]["name"] == "echo"
    assert body["_meta"]["transport"] == "streamable-http"
    assert body["_meta"]["session_id"] == "sess-abc"


def test_streamable_http_401_needs_reauth(agent_client):
    mock_response = httpx.Response(401, text="unauthorized")

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        resp = agent_client.post("/rpc", json=_http_payload())

    body = resp.json()
    assert body["error"]["code"] == -32001
    assert body["_meta"]["needs_reauth"] is True


def test_websocket_transport_not_implemented(agent_client):
    resp = agent_client.post(
        "/rpc",
        json={
            "server_slug": "ws",
            "transport": "websocket",
            "method": "tools/list",
            "jsonrpc_id": 2,
            "upstream": {
                "url": "wss://mcp.example.com/ws",
                "allowed_hosts": ["mcp.example.com"],
            },
        },
    )
    assert resp.json()["error"]["code"] == -32004


def test_stdio_backward_compat_unchanged(agent_client):
    resp = agent_client.post(
        "/rpc",
        json={
            "server_slug": "stub-server",
            "command": "python3",
            "args": [str(STDIO_STUB)],
            "method": "tools/list",
            "jsonrpc_id": 3,
        },
    )
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    assert any(t["name"] == "echo" for t in tools)


def test_health_includes_connection_count(agent_client):
    resp = agent_client.get("/health")
    body = resp.json()
    assert "connection_count" in body
    assert "streamable-http" in body["connection_count"]
