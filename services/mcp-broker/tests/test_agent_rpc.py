"""Unit tests for the in-container sandbox-agent POST /rpc stdio bridge."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
SANDBOX_IMAGE = REPO_ROOT / "services/mcp-broker/sandbox-image"
STDIO_STUB = REPO_ROOT / "services/mcp-broker/tests/fixtures/stdio_mcp_stub.py"
OAUTH_HANG_STUB = REPO_ROOT / "services/mcp-broker/tests/fixtures/oauth_hang_stub.py"


def _load_agent_app(monkeypatch):
    monkeypatch.setenv("ORG_SLUG", "test-org")
    sandbox_image = str(SANDBOX_IMAGE)
    if sandbox_image not in sys.path:
        sys.path.insert(0, sandbox_image)
    for mod in ("agent.main", "agent.stdio_manager", "agent"):
        sys.modules.pop(mod, None)
    main_mod = importlib.import_module("agent.main")
    return main_mod.app


@pytest.fixture
def agent_client(monkeypatch):
    app = _load_agent_app(monkeypatch)
    with TestClient(app) as client:
        yield client


def _rpc_payload(**overrides):
    base = {
        "server_slug": "stub-server",
        "command": "python3",
        "args": [str(STDIO_STUB)],
        "method": "tools/list",
        "jsonrpc_id": 1,
    }
    base.update(overrides)
    return base


def test_rpc_initialize_returns_cached_capabilities(agent_client):
    resp = agent_client.post(
        "/rpc",
        json=_rpc_payload(method="initialize", jsonrpc_id=42),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 42
    assert "result" in body
    assert body["result"]["protocolVersion"] == "2024-11-05"
    assert "stdio" in body["result"]["serverInfo"]["name"]


def test_rpc_tools_list_via_stdio_child(agent_client):
    resp = agent_client.post("/rpc", json=_rpc_payload(jsonrpc_id=7))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 7
    assert "result" in body
    tools = body["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"echo", "get_env"}.issubset(names)


def test_rpc_tools_call_echo(agent_client):
    resp = agent_client.post(
        "/rpc",
        json=_rpc_payload(
            method="tools/call",
            params={
                "name": "echo",
                "arguments": {"msg": "hello-sandbox"},
            },
            jsonrpc_id=9,
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 9
    text = body["result"]["content"][0]["text"]
    assert text == "hello-sandbox"


def test_rpc_reuses_spawned_process(agent_client):
    first = agent_client.post("/rpc", json=_rpc_payload(jsonrpc_id=1))
    second = agent_client.post("/rpc", json=_rpc_payload(jsonrpc_id=2))
    assert first.status_code == 200 and second.status_code == 200

    health = agent_client.get("/health")
    assert health.json()["process_count"] == 1


def test_env_denylist_blocks_gateway_secret(agent_client, monkeypatch):
    monkeypatch.setenv("GATEWAY_INTERNAL_API_KEY", "super-secret-gateway-key")
    resp = agent_client.post(
        "/rpc",
        json=_rpc_payload(
            env={"GATEWAY_INTERNAL_API_KEY": "injected-too", "SAFE_TEST_VAR": "visible"},
            method="tools/call",
            params={
                "name": "get_env",
                "arguments": {"key": "GATEWAY_INTERNAL_API_KEY"},
            },
            jsonrpc_id=11,
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["content"][0]["text"] == ""

    safe = agent_client.post(
        "/rpc",
        json=_rpc_payload(
            env={"SAFE_TEST_VAR": "visible"},
            method="tools/call",
            params={"name": "get_env", "arguments": {"key": "SAFE_TEST_VAR"}},
            jsonrpc_id=12,
        ),
    )
    assert safe.json()["result"]["content"][0]["text"] == "visible"


def test_oauth_hang_surfaces_reauth_error(agent_client):
    resp = agent_client.post(
        "/rpc",
        json=_rpc_payload(
            server_slug="oauth-hang",
            command="python3",
            args=[str(OAUTH_HANG_STUB)],
            jsonrpc_id=13,
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    msg = body["error"]["message"].lower()
    assert "authentication" in msg or "re-authenticate" in msg


def test_disallowed_command_rejected(agent_client):
    resp = agent_client.post(
        "/rpc",
        json=_rpc_payload(command="bash", args=["-c", "echo hi"], jsonrpc_id=14),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert "not permitted" in body["error"]["message"].lower()


def test_health_reports_org_slug(agent_client):
    resp = agent_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["org_slug"] == "test-org"
    assert body["service"] == "mcp-sandbox-agent"
