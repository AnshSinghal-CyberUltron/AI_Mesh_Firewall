"""Unit tests for demo MCP proxy helpers + snippet generation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import auth as demo_auth  # noqa: E402
import main as demo_main  # noqa: E402
import sdk_snippets as snippets  # noqa: E402


def test_mcp_tool_call_snippet_includes_server_and_tool():
    code = snippets.mcp_tool_call_snippet("everything-1", "echo", {"message": "hi"})
    assert "everything-1" in code
    assert "echo" in code
    assert "mcp-connector/tools/call" in code


def test_mcp_jsonrpc_snippet_uses_gateway_not_v1():
    code = snippets.mcp_jsonrpc_snippet(
        "zeroshield", "everything-1", "echo", {"message": "hi"}, gateway_host="http://gw:8300"
    )
    assert "ORG = " in code and "zeroshield" in code
    assert "SERVER = " in code and "everything-1" in code
    assert "tools/call" in code
    assert "http://gw:8300" in code
    assert "/v1/gateway" not in code


def test_sort_mcp_servers_prefers_connected_with_tools():
    rows = [
        {"id": 1, "name": "a", "connection_status": "failed", "tools_count": 9},
        {"id": 2, "name": "b", "connection_status": "connected", "tools_count": 0},
        {"id": 3, "name": "c", "connection_status": "connected", "tools_count": 5},
    ]
    sorted_rows = demo_main._sort_mcp_servers(rows)
    assert sorted_rows[0]["id"] == 3


@pytest.fixture()
def client(monkeypatch):
    user = {
        "id": 1,
        "email": "admin@example.com",
        "organization": {"id": 2, "slug": "zeroshield", "name": "ZeroShield"},
        "_access_token": "tok",
    }
    # Override the SAME function object Depends() captured at import time.
    demo_main.app.dependency_overrides[demo_auth.require_user] = lambda: user
    monkeypatch.setattr(
        demo_main.session_keys,
        "gateway_key_for_user",
        lambda u: ("zs_test_key", {"org_slug": "zeroshield", "prefix": "zs_test"}),
    )
    with TestClient(demo_main.app) as c:
        yield c
    demo_main.app.dependency_overrides.clear()


def test_mcp_servers_proxy(client, monkeypatch):
    monkeypatch.setattr(
        demo_main,
        "_control_get",
        lambda user, path, **kw: {
            "results": [
                {
                    "id": "9",
                    "server_slug": "everything-1",
                    "name": "Everything",
                    "connection_status": "connected",
                    "tools_count": 3,
                    "transport": "stdio",
                }
            ]
        },
    )
    r = client.get("/api/mcp/servers")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["servers"][0]["server_slug"] == "everything-1"


def test_mcp_tools_call_proxy(client, monkeypatch):
    monkeypatch.setattr(
        demo_main,
        "_control_post",
        lambda user, path, body, **kw: (
            200,
            {"decision": "allow", "result": {"content": [{"text": "Echo: hi"}]}},
        ),
    )
    r = client.post(
        "/api/mcp/tools/call",
        json={"server_slug": "everything-1", "name": "echo", "arguments": {"message": "hi"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "allow"
    assert "mcp-connector/tools/call" in body["sdk_snippet"]


def test_mcp_gateway_endpoint(client):
    r = client.get("/api/mcp/gateway-endpoint")
    assert r.status_code == 200
    body = r.json()
    assert body["org_slug"] == "zeroshield"
    assert "{server_slug}" in body["mcp_url_template"]
    assert body["mcp_url_template"].endswith("/gateway/{org_slug}/mcp/{server_slug}")


def test_mcp_servers_unauthorized():
    demo_main.app.dependency_overrides.clear()
    with TestClient(demo_main.app) as c:
        r = c.get("/api/mcp/servers")
        assert r.status_code == 401
