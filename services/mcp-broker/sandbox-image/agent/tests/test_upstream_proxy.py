"""Agent-side unit tests for HTTP/SSE upstream proxy (P4.9)."""

from __future__ import annotations

import importlib
import json
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
        "agent.ws_manager",
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


class _FakeStreamCtx:
    """Fake httpx streaming response (SSE) context manager — the modern streamable-
    http upstream keeps the SSE connection open, so the agent must stream-read it."""

    def __init__(self, *, status=200, headers=None, sse_frames=None, text=""):
        self.status_code = status
        self.headers = headers or {}
        self._sse = sse_frames or []
        self.text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def aread(self):
        return self.text.encode()

    async def aiter_lines(self):
        for payload in self._sse:
            yield "event: message"
            yield "data: " + json.dumps(payload)
            yield ""


def _fake_stream(*, result=None, err_status=None, session_id="sess-abc"):
    """Return a patch target for httpx.AsyncClient.stream that echoes the request id
    (so both the auto-initialize handshake and the real method get a matching reply)."""
    def _stream(self, _method, _url, *, json=None, headers=None, timeout=None):  # noqa: A002
        if err_status:
            return _FakeStreamCtx(status=err_status, headers={"content-type": "text/plain"}, text="unauthorized")
        req_id = (json or {}).get("id")
        return _FakeStreamCtx(
            headers={"content-type": "text/event-stream", "mcp-session-id": session_id},
            sse_frames=[{"jsonrpc": "2.0", "id": req_id, "result": result if result is not None else {}}],
        )
    return _stream


def test_streamable_http_tools_list(agent_client):
    with patch("httpx.AsyncClient.stream", new=_fake_stream(result={"tools": [{"name": "echo"}]})), \
         patch("httpx.AsyncClient.post", new=AsyncMock(return_value=httpx.Response(202))):
        resp = agent_client.post("/rpc", json=_http_payload())

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["tools"][0]["name"] == "echo"
    assert body["_meta"]["transport"] == "streamable-http"
    assert body["_meta"]["session_id"] == "sess-abc"


def test_streamable_http_401_needs_reauth(agent_client):
    # A 401 during the (auto) handshake surfaces as -32001 needs_reauth.
    with patch("httpx.AsyncClient.stream", new=_fake_stream(err_status=401)), \
         patch("httpx.AsyncClient.post", new=AsyncMock(return_value=httpx.Response(202))):
        resp = agent_client.post("/rpc", json=_http_payload())

    body = resp.json()
    assert body["error"]["code"] == -32001
    assert body["_meta"]["needs_reauth"] is True


def test_streamable_http_stale_session_recovers(agent_client):
    # An upstream that restarted rejects the cached session ("No valid session ID");
    # the agent must invalidate + re-handshake + retry ONCE and succeed.
    state = {"real_method_calls": 0}

    def _stream(self, _method, _url, *, json=None, headers=None, timeout=None):  # noqa: A002
        req = json or {}
        rid = req.get("id")
        if req.get("method") == "initialize":
            return _FakeStreamCtx(
                headers={"content-type": "text/event-stream", "mcp-session-id": "sess-new"},
                sse_frames=[{"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05"}}],
            )
        state["real_method_calls"] += 1
        if state["real_method_calls"] == 1:
            return _FakeStreamCtx(status=400, headers={"content-type": "text/plain"},
                                  text='{"error":{"message":"No valid session ID provided"}}')
        return _FakeStreamCtx(
            headers={"content-type": "text/event-stream", "mcp-session-id": "sess-new"},
            sse_frames=[{"jsonrpc": "2.0", "id": rid, "result": {"tools": [{"name": "echo"}]}}],
        )

    with patch("httpx.AsyncClient.stream", new=_stream), \
         patch("httpx.AsyncClient.post", new=AsyncMock(return_value=httpx.Response(202))):
        resp = agent_client.post("/rpc", json=_http_payload())

    body = resp.json()
    assert body["result"]["tools"][0]["name"] == "echo"  # recovered after re-init
    assert state["real_method_calls"] == 2  # first rejected, second (post re-init) succeeded


def _ws_payload(**overrides):
    base = {
        "server_slug": "ws-server",
        "transport": "websocket",
        "method": "tools/list",
        "jsonrpc_id": 2,
        "upstream": {
            "url": "wss://mcp.example.com/ws",
            "allowed_hosts": ["mcp.example.com"],
            "headers": {"Authorization": "Bearer ws-token"},
            "oauth_client_role": "forbidden_in_sandbox",
        },
    }
    base.update(overrides)
    return base


class _FakeWebSocket:
    def __init__(self, recv_payload: str):
        self._recv_payload = recv_payload
        self.state = type("State", (), {"name": "OPEN"})()

    async def send(self, _data: str) -> None:
        return None

    async def recv(self) -> str:
        return self._recv_payload

    async def close(self) -> None:
        return None


def test_websocket_tools_list(agent_client):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "ws-tool"}]}}
    )
    fake_ws = _FakeWebSocket(payload)

    with patch("websockets.connect", new=AsyncMock(return_value=fake_ws)):
        resp = agent_client.post("/rpc", json=_ws_payload())

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["tools"][0]["name"] == "ws-tool"
    assert body["_meta"]["transport"] == "websocket"


def test_websocket_handshake_401_needs_reauth(agent_client):
    from websockets.exceptions import InvalidStatus

    class _Resp:
        status_code = 401

    with patch(
        "websockets.connect",
        new=AsyncMock(side_effect=InvalidStatus(_Resp())),
    ):
        resp = agent_client.post("/rpc", json=_ws_payload())

    body = resp.json()
    assert body["error"]["code"] == -32001
    assert body["_meta"]["needs_reauth"] is True


def test_websocket_egress_denied(agent_client):
    resp = agent_client.post(
        "/rpc",
        json=_ws_payload(
            upstream={
                "url": "wss://evil.example.com/ws",
                "allowed_hosts": ["mcp.example.com"],
                "headers": {},
            }
        ),
    )
    assert resp.json()["error"]["code"] == -32002


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
