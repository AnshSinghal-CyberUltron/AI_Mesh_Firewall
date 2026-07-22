"""OAuth token mirror: control plane → gateway Redis for HTTP OAuth servers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from ai_mesh_gateway import mcp_proxy


def _mirror_req(body: dict, *, key: str = "test-internal-key") -> Request:
    raw = json.dumps(body).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/mcp/internal/oauth-token-mirror",
        "headers": [
            (b"x-gateway-internal-key", key.encode()),
            (b"content-type", b"application/json"),
        ],
    }

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


@pytest.mark.asyncio
async def test_oauth_token_mirror_requires_internal_key(monkeypatch):
    monkeypatch.setattr(mcp_proxy, "_valid_internal_key", lambda k: k == "good-key")
    resp = await mcp_proxy.internal_oauth_token_mirror(
        _mirror_req({"org_slug": "zeroshield", "server_url": "https://mcp.example/mcp"}, key="bad")
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_oauth_token_mirror_persists_token(monkeypatch):
    monkeypatch.setattr(mcp_proxy, "_valid_internal_key", lambda _k: True)
    saved = {}

    async def _fake_save(org_slug, server_url, data):
        saved["org_slug"] = org_slug
        saved["server_url"] = server_url
        saved["data"] = data

    monkeypatch.setattr("mcp_oauth_proxy._token_save", _fake_save)

    resp = await mcp_proxy.internal_oauth_token_mirror(
        _mirror_req(
            {
                "org_slug": "zeroshield",
                "server_url": "https://mcp.linear.app/mcp",
                "access_token": "lin_test_token",
                "refresh_token": "lin_refresh",
                "expires_in": 3600,
                "token_endpoint": "https://api.linear.app/oauth/token",
                "client_id": "cid",
            },
            key="ok",
        )
    )
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["mirrored"] is True
    assert saved["org_slug"] == "zeroshield"
    assert saved["server_url"] == "https://mcp.linear.app/mcp"
    assert saved["data"]["access_token"] == "lin_test_token"
    assert saved["data"]["refresh_token"] == "lin_refresh"


@pytest.mark.asyncio
async def test_maybe_propagate_upstream_needs_reauth_on_32001(monkeypatch):
    notified = []

    async def _capture(org_slug, server_slug, reason):
        notified.append((org_slug, server_slug, reason))

    monkeypatch.setattr(mcp_proxy, "_notify_control_needs_reauth", _capture)

    await mcp_proxy._maybe_propagate_upstream_needs_reauth(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32001, "message": "upstream returned 401; re-authenticate"},
            "_meta": {"needs_reauth": True},
        },
        "zeroshield",
        "linear-manual-oauth",
    )
    assert len(notified) == 1
    assert notified[0][0] == "zeroshield"
    assert notified[0][1] == "linear-manual-oauth"
