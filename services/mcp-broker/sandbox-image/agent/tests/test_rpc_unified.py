"""Prove single POST /rpc entrypoint for all MCP transports (P4.11)."""

from __future__ import annotations

import importlib
import json
import sys
from contextlib import asynccontextmanager
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




@pytest.fixture(autouse=True)
def _skip_upstream_ssrf_dns(agent_client):
    """Unit tests mock upstream I/O; skip live DNS resolution for mcp.example.com."""
    with patch("agent.upstream_manager._assert_upstream_not_ssrf", new=AsyncMock()):
        yield

def test_single_post_rpc_route(agent_client):
    """Agent exposes exactly one JSON-RPC forward endpoint."""
    app = agent_client.app
    post_rpc = [
        r
        for r in app.routes
        if getattr(r, "path", None) == "/rpc" and "POST" in getattr(r, "methods", set())
    ]
    assert len(post_rpc) == 1


@pytest.mark.parametrize(
    "transport",
    ["stdio", "streamable-http", "sse", "websocket"],
)
def test_all_transports_accepted_on_post_rpc(agent_client, transport):
    """Each transport is dispatched via POST /rpc (not separate agent routes)."""
    if transport == "stdio":
        payload = {
            "server_slug": "u-stub",
            "transport": "stdio",
            "stdio": {"command": "python3", "args": [str(STDIO_STUB)], "env": {}},
            "method": "initialize",
            "jsonrpc_id": 100,
        }
        resp = agent_client.post("/rpc", json=payload)
        assert resp.status_code == 200
        assert "result" in resp.json()
        return

    if transport == "streamable-http":
        mock_response = httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 101, "result": {"tools": []}},
        )
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            resp = agent_client.post(
                "/rpc",
                json={
                    "server_slug": "u-http",
                    "transport": transport,
                    "method": "tools/list",
                    "jsonrpc_id": 101,
                    "upstream": {
                        "url": "https://mcp.example.com/mcp",
                        "allowed_hosts": ["mcp.example.com"],
                        "headers": {},
                    },
                },
            )
        assert resp.status_code == 200
        assert resp.json().get("_meta", {}).get("transport") == transport
        return

    if transport == "sse":
        pending: list[dict] = []

        class _FakeSseGet:
            status_code = 200
            headers = {"content-type": "text/event-stream"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def aiter_lines(self):
                yield "event: endpoint"
                yield "data: /messages?sessionId=sess-unified"
                yield ""
                delivered = 0
                while delivered < 2:
                    if pending:
                        payload = pending.pop(0)
                        yield "event: message"
                        yield "data: " + json.dumps(payload)
                        yield ""
                        delivered += 1
                    else:
                        import asyncio
                        await asyncio.sleep(0.01)

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
            if method == "GET":
                return _FakeSseGet()
            if req.get("method") == "initialize":
                pending.append(
                    {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05"}}
                )
            else:
                pending.append(
                    {"jsonrpc": "2.0", "id": rid, "result": {"tools": [{"name": "sse-tool"}]}}
                )
            return _FakePostStream()

        with patch("httpx.AsyncClient.stream", side_effect=_stream):
            resp = agent_client.post(
                "/rpc",
                json={
                    "server_slug": "u-sse",
                    "transport": "sse",
                    "method": "tools/list",
                    "jsonrpc_id": 102,
                    "upstream": {
                        "url": "https://mcp.example.com/sse",
                        "allowed_hosts": ["mcp.example.com"],
                        "headers": {},
                    },
                },
            )
        assert resp.status_code == 200
        assert resp.json()["result"]["tools"][0]["name"] == "sse-tool"
        return

    # websocket
    class _FakeWS:
        def __init__(self):
            self.state = type("S", (), {"name": "OPEN"})()

        async def send(self, _d):
            return None

        async def recv(self):
            return json.dumps(
                {"jsonrpc": "2.0", "id": 103, "result": {"tools": [{"name": "ws-unified"}]}}
            )

        async def close(self):
            return None

    with patch("websockets.connect", new=AsyncMock(return_value=_FakeWS())):
        resp = agent_client.post(
            "/rpc",
            json={
                "server_slug": "u-ws",
                "transport": "websocket",
                "method": "tools/list",
                "jsonrpc_id": 103,
                "upstream": {
                    "url": "wss://mcp.example.com/ws",
                    "allowed_hosts": ["mcp.example.com"],
                    "headers": {},
                },
            },
        )
    assert resp.status_code == 200
    assert resp.json()["result"]["tools"][0]["name"] == "ws-unified"


def test_unknown_transport_rejected_on_post_rpc(agent_client):
    resp = agent_client.post(
        "/rpc",
        json={
            "server_slug": "bad",
            "transport": "grpc",
            "method": "ping",
            "jsonrpc_id": 1,
        },
    )
    # pydantic validation error → 422, or if coerced would be -32004
    assert resp.status_code in (200, 422)
    if resp.status_code == 200:
        assert resp.json()["error"]["code"] == -32004


# ── CHG-0121: the agent /rpc endpoint logs the propagated X-Request-ID (last trace hop)
# with SAFE metadata ONLY — never params/args/env/upstream (mirrors broker CHG-0052).


def test_rpc_logs_x_request_id_safely(agent_client, caplog):
    import logging
    import sys

    agent_main = sys.modules["agent.main"]
    with patch.object(
        agent_main, "send_jsonrpc",
        new=AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}),
    ):
        with caplog.at_level(logging.INFO):
            resp = agent_client.post(
                "/rpc",
                headers={"X-Request-ID": "trace-agent-7"},
                json={"server_slug": "srv", "transport": "stdio", "method": "tools/list",
                      "jsonrpc_id": 1, "stdio": {"command": "npx", "args": ["-y", "secret-pkg"]}},
            )
    assert resp.status_code == 200
    msgs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("agent rpc")]
    assert any("request_id=trace-agent-7" in m for m in msgs)              # correlated
    assert all("npx" not in m and "secret-pkg" not in m and "command" not in m
               for m in msgs)                                              # no args/command leaked
