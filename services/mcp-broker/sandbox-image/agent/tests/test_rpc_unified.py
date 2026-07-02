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
        class _FakeStream:
            status_code = 200

            async def aiter_lines(self):
                yield "event: endpoint"
                yield "data: /messages?sessionId=sess-unified"
                yield ""

        @asynccontextmanager
        async def _fake_stream(*_a, **_k):
            yield _FakeStream()

        mock_post = httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 102, "result": {"tools": [{"name": "sse-tool"}]}},
        )

        with (
            patch("httpx.AsyncClient.stream", side_effect=_fake_stream),
            patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_post)),
        ):
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
