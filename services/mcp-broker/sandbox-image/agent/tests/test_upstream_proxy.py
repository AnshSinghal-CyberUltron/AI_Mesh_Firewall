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
    # CHG-0067: the SSRF guard in _get_session resolves the upstream host via getaddrinfo.
    # These tests use mcp.example.com (NXDOMAIN here) and mock the socket, so bypass the
    # DNS-resolve guard to stay hermetic; the dedicated SSRF test un-sets this to exercise it.
    monkeypatch.setenv("MCP_AGENT_ALLOW_INTERNAL_HOSTS", "1")
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

    async def aiter_bytes(self):
        # CHG-0066: the JSON branch reads incrementally via aiter_bytes(). CHG-0130: the
        # SSE branch now also reads via aiter_bytes() (bounded line reader), so yield the
        # SSE frames as bytes when present; else yield the text body in a few chunks.
        if self._sse:
            for payload in self._sse:
                yield b"event: message\n"
                yield ("data: " + json.dumps(payload) + "\n").encode()
                yield b"\n"
            return
        data = self.text.encode()
        for i in range(0, max(len(data), 1), 64):
            yield data[i:i + 64]

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


def test_streamable_http_empty_tools_list_retries(agent_client):
    """Cold/stale upstream sessions can return tools=[] once; agent must re-init + retry."""
    calls = {"n": 0}

    def _stream(self, _method, _url, *, json=None, headers=None, timeout=None):  # noqa: A002
        calls["n"] += 1
        req = json or {}
        rid = req.get("id")
        method = req.get("method")
        if method == "initialize":
            return _FakeStreamCtx(
                headers={"content-type": "text/event-stream", "mcp-session-id": "sess-retry"},
                sse_frames=[{"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05"}}],
            )
        tools = [] if calls["n"] <= 2 else [{"name": "echo"}]
        return _FakeStreamCtx(
            headers={"content-type": "text/event-stream", "mcp-session-id": "sess-retry"},
            sse_frames=[{"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}],
        )

    with patch("httpx.AsyncClient.stream", new=_stream), \
         patch("httpx.AsyncClient.post", new=AsyncMock(return_value=httpx.Response(202))):
        resp = agent_client.post("/rpc", json=_http_payload())

    body = resp.json()
    assert body["result"]["tools"][0]["name"] == "echo"
    assert calls["n"] >= 3  # init + empty list + re-init + populated list


def test_sse_cold_tools_list_auto_inits(agent_client):
    """SSE must auto-initialize on the first tools/list (same as streamable-http)."""
    pending: list[dict] = []
    init_seen = {"n": 0}

    class _FakeSseGet:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def aiter_lines(self):
            yield "event: endpoint"
            yield "data: /messages?sessionId=sess-sse-cold"
            yield ""
            delivered = 0
            while delivered < 4:
                if pending:
                    payload = pending.pop(0)
                    yield "event: message"
                    yield "data: " + json.dumps(payload)
                    yield ""
                    delivered += 1
                else:
                    import asyncio
                    await asyncio.sleep(0.01)

        async def aiter_bytes(self):
            # CHG-0130: the reader now reads via aiter_bytes(); reuse the line generator.
            async for ln in self.aiter_lines():
                yield (ln + "\n").encode()

    class _FakePostStream:
        status_code = 202
        headers = {"content-type": "text/plain"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def aread(self):
            return b"Accepted"

    def _stream(method, *_a, **kw):
        req = kw.get("json") or {}
        rid = req.get("id")
        m = req.get("method")
        if method == "GET":
            return _FakeSseGet()
        if m == "initialize":
            init_seen["n"] += 1
            pending.append(
                {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05"}}
            )
        else:
            pending.append(
                {"jsonrpc": "2.0", "id": rid, "result": {"tools": [{"name": "sse-echo"}]}}
            )
        return _FakePostStream()

    with patch("httpx.AsyncClient.stream", side_effect=_stream):
        resp = agent_client.post(
            "/rpc",
            json={
                "server_slug": "sse-cold",
                "transport": "sse",
                "method": "tools/list",
                "jsonrpc_id": 55,
                "upstream": {
                    "url": "https://mcp.example.com/sse",
                    "allowed_hosts": ["mcp.example.com"],
                    "headers": {},
                },
            },
        )

    body = resp.json()
    assert body["result"]["tools"][0]["name"] == "sse-echo"
    assert init_seen["n"] == 1


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


@pytest.mark.asyncio
async def test_ws_connect_enforces_response_cap_via_max_size():
    # CHG-0131: the ws library-level frame cap must match the agent's configured
    # _MAX_RESPONSE_BYTES — else websockets' 1 MiB default silently overrides it
    # (ignoring a raised cap; under-enforcing a lowered one) on the ws transport only.
    from agent import ws_manager
    from agent.upstream_manager import UpstreamSession, _MAX_RESPONSE_BYTES

    connect_mock = AsyncMock(return_value=_FakeWebSocket("{}"))
    sess = UpstreamSession(
        server_slug="srv", transport="websocket", url="wss://up.example/ws",
        allowed_hosts=[], headers={},
    )
    with patch("websockets.connect", new=connect_mock):
        await ws_manager.ensure_ws_connected(sess, connect_timeout=1.0)
    assert connect_mock.call_args.kwargs.get("max_size") == _MAX_RESPONSE_BYTES


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


# ── CHG-0066: the JSON (non-SSE) upstream-response branch buffered the WHOLE body
# via response.aread() then checked the size — so an untrusted upstream could OOM the
# sandbox agent (up to its mem limit) before the check. It now reads incrementally via
# aiter_bytes() and aborts at _MAX_RESPONSE_BYTES (parity with the SSE branch).
def _fake_stream_json(*, result=None, session_id="sess-abc", pad_bytes=0):
    def _stream(self, _method, _url, *, json=None, headers=None, timeout=None):  # noqa: A002
        req_id = (json or {}).get("id")
        body = {"jsonrpc": "2.0", "id": req_id,
                "result": result if result is not None else {}}
        text = __import__("json").dumps(body)
        if pad_bytes:
            text = __import__("json").dumps(
                {"jsonrpc": "2.0", "id": req_id, "result": {"pad": "x" * pad_bytes}})
        return _FakeStreamCtx(
            headers={"content-type": "application/json", "mcp-session-id": session_id},
            text=text,
        )
    return _stream


def test_streamable_http_json_response_over_cap_rejected(agent_client):
    with patch("agent.upstream_manager._MAX_RESPONSE_BYTES", 100), \
         patch("httpx.AsyncClient.stream", new=_fake_stream_json(pad_bytes=5000)), \
         patch("httpx.AsyncClient.post", new=AsyncMock(return_value=httpx.Response(202))):
        resp = agent_client.post("/rpc", json=_http_payload())
    body = resp.json()
    assert body["error"]["code"] == -32000
    assert "too large" in body["error"]["message"].lower()


def test_streamable_http_json_response_under_cap_ok(agent_client):
    # the JSON branch still works normally with the incremental reader
    with patch("httpx.AsyncClient.stream",
               new=_fake_stream_json(result={"tools": [{"name": "echo"}]})), \
         patch("httpx.AsyncClient.post", new=AsyncMock(return_value=httpx.Response(202))):
        resp = agent_client.post("/rpc", json=_http_payload())
    assert resp.status_code == 200
    assert resp.json()["result"]["tools"][0]["name"] == "echo"


# ── CHG-0067: sandbox-side SSRF / DNS-rebinding guard. _validate_upstream matches the
# host STRING against allowed_hosts but never resolves it; an allowlisted host that
# RESOLVES to an internal/loopback/link-local/cloud-metadata IP would be dialed from the
# sandbox (which has open-NAT egress). _assert_upstream_not_ssrf resolves + blocks it.
def test_streamable_http_ssrf_blocks_internal_resolving_host(agent_client, monkeypatch):
    monkeypatch.delenv("MCP_AGENT_ALLOW_INTERNAL_HOSTS", raising=False)  # enable the guard
    payload = _http_payload(upstream={
        "url": "https://localhost/mcp",          # allowlisted host that resolves to 127.0.0.1
        "allowed_hosts": ["localhost"],
        "headers": {},
        "oauth_client_role": "forbidden_in_sandbox",
    })
    # No httpx mock needed — the SSRF guard rejects BEFORE any connection is opened.
    resp = agent_client.post("/rpc", json=payload)
    body = resp.json()
    assert body["error"]["code"] == -32002
    msg = body["error"]["message"].lower()
    assert "egress denied" in msg and ("loopback" in msg or "internal" in msg or "127.0.0.1" in msg)


def test_streamable_http_ssrf_allows_public_resolved_host(agent_client, monkeypatch):
    monkeypatch.delenv("MCP_AGENT_ALLOW_INTERNAL_HOSTS", raising=False)  # guard active
    # resolve the allowlisted host to a PUBLIC IP so the guard permits it
    monkeypatch.setattr("socket.getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    with patch("httpx.AsyncClient.stream", new=_fake_stream(result={"tools": [{"name": "echo"}]})), \
         patch("httpx.AsyncClient.post", new=AsyncMock(return_value=httpx.Response(202))):
        resp = agent_client.post("/rpc", json=_http_payload())
    assert resp.status_code == 200
    assert resp.json()["result"]["tools"][0]["name"] == "echo"


# ── CHG-0069: the error path did `(await response.aread())[:500]` — aread() buffers the
# WHOLE untrusted error body before the slice, so a malicious upstream returning a huge
# 4xx/5xx body OOMs the sandbox agent. _aread_snippet reads a bounded amount + stops.
def test_streamable_http_500_error_body_is_bounded(agent_client):
    with patch("httpx.AsyncClient.stream", new=_fake_stream(err_status=500)), \
         patch("httpx.AsyncClient.post", new=AsyncMock(return_value=httpx.Response(202))):
        resp = agent_client.post("/rpc", json=_http_payload())
    body = resp.json()
    assert body["error"]["code"] == -32000
    assert "upstream HTTP 500" in body["error"]["message"]
    assert "unauthorized" in body["error"]["message"]  # the (bounded) body snippet


@pytest.mark.asyncio
async def test_aread_snippet_bounds_and_stops_early(monkeypatch):
    _load_agent_app(monkeypatch)  # ensure agent.upstream_manager is importable
    from agent.upstream_manager import _aread_snippet

    class _BigResp:
        def __init__(self):
            self.consumed = 0

        async def aiter_bytes(self):
            for _ in range(1000):        # a "huge" error body
                self.consumed += 1
                yield b"x" * 100

    r = _BigResp()
    out = await _aread_snippet(r, limit=250)
    assert len(out) <= 250
    assert r.consumed <= 3               # stopped after crossing 250B, NOT all 1000 chunks
