"""Server-disable enforcement parity for the bare REST MCP routes.

Regression for the residual half of the server-disable bug: the JSON-RPC route
(org_mcp_jsonrpc) blocks a server whose registration has is_active=False or
is_exposed_to_agents=False (via _server_disabled), but the bare REST routes
(org_mcp_tool_call / org_mcp_tools_list / org_mcp_server_health) proxied straight
through after only the org-scope check — so a disabled/unexposed server stayed fully
callable via REST. These tests pin the fix: all three REST routes now 403 on a
disabled server, and still proceed when the server is enabled.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402
from middleware import AuthContext  # noqa: E402

_DISABLED = {"transport": "stdio", "is_active": False, "is_exposed_to_agents": True}
_UNEXPOSED = {"transport": "stdio", "is_active": True, "is_exposed_to_agents": False}
_ENABLED = {"transport": "stdio", "is_active": True, "is_exposed_to_agents": True}


def _auth(org_slug="demo"):
    return AuthContext(key_hash="h" * 64, payload={
        "key_id": "k1", "user_id": 1, "project_id": "p1", "org_slug": org_slug,
        "prefix": "kpref", "mcp_allowed_tools": [], "mcp_max_tool_calls": 0,
    })


def _req(body_obj=None):
    r = SimpleNamespace(state=SimpleNamespace(auth_context=_auth()))
    r.headers = {}
    r.body = AsyncMock(return_value=json.dumps(body_obj or {}).encode())
    return r


def _decode(resp):
    return json.loads(bytes(resp.body))


@pytest.mark.asyncio
@pytest.mark.parametrize("cfg", [_DISABLED, _UNEXPOSED], ids=["is_active_false", "unexposed"])
async def test_rest_helper_blocks_disabled(cfg):
    rec = AsyncMock()
    with (
        patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value=cfg)),
        patch.object(mcp_proxy, "_record_gateway_event", rec),
    ):
        resp = await mcp_proxy._rest_server_disabled_response(_req(), "demo", "srv")
    assert resp is not None and resp.status_code == 403
    assert _decode(resp)["reason"] == "server_disabled"
    # audited as a caller-org block
    assert rec.await_count == 1
    assert rec.await_args.kwargs.get("reason") == "server_disabled"
    assert rec.await_args.kwargs.get("decision") == "block"


@pytest.mark.asyncio
async def test_rest_helper_allows_enabled():
    with patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value=_ENABLED)):
        resp = await mcp_proxy._rest_server_disabled_response(_req(), "demo", "srv")
    assert resp is None


@pytest.mark.asyncio
async def test_rest_helper_fails_open_on_missing_config():
    # control-plane hiccup (None config) must NOT block every call — fail open.
    with patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value=None)):
        resp = await mcp_proxy._rest_server_disabled_response(_req(), "demo", "srv")
    assert resp is None


@pytest.mark.asyncio
async def test_tool_call_route_blocks_disabled_before_backend():
    """org_mcp_tool_call must 403 on a disabled server and never reach the backend."""
    backend = AsyncMock(side_effect=AssertionError("backend must not be reached"))
    with (
        patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value=_DISABLED)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy.httpx, "AsyncClient", backend),
    ):
        resp = await mcp_proxy.org_mcp_tool_call(
            "demo", "srv", _req({"name": "echo", "arguments": {"m": "hi"}})
        )
    assert resp.status_code == 403
    assert _decode(resp)["reason"] == "server_disabled"


@pytest.mark.asyncio
async def test_tools_list_route_blocks_disabled():
    backend = AsyncMock(side_effect=AssertionError("backend must not be reached"))
    with (
        patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value=_UNEXPOSED)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy.httpx, "AsyncClient", backend),
    ):
        resp = await mcp_proxy.org_mcp_tools_list("demo", "srv", _req())
    assert resp.status_code == 403
    assert _decode(resp)["reason"] == "server_disabled"


@pytest.mark.asyncio
async def test_health_route_blocks_disabled():
    backend = AsyncMock(side_effect=AssertionError("backend must not be reached"))
    with (
        patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value=_DISABLED)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy.httpx, "AsyncClient", backend),
    ):
        resp = await mcp_proxy.org_mcp_server_health("demo", "srv", _req())
    assert resp.status_code == 403
    assert _decode(resp)["reason"] == "server_disabled"


# ── Internal routes (control-plane MCPToolCallView / MCPToolListView proxy here) ──

def _internal_req(body_obj):
    r = SimpleNamespace(state=SimpleNamespace(auth_context=None))
    r.headers = {"X-Gateway-Internal-Key": "k"}
    r.body = AsyncMock(return_value=json.dumps(body_obj).encode())
    r.json = AsyncMock(return_value=body_obj)
    return r


@pytest.mark.asyncio
async def test_internal_tools_call_blocks_disabled():
    """internal_tools_call (control-plane MCPToolCallView path) must block a disabled server."""
    with (
        patch.object(mcp_proxy, "_valid_internal_key", lambda k: True),
        patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value=_UNEXPOSED)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "_get_enabled_tools",
                     AsyncMock(side_effect=AssertionError("must block before tool lookup"))),
    ):
        resp = await mcp_proxy.internal_tools_call(
            _internal_req({"org_slug": "demo", "server_slug": "srv",
                           "tool_name": "echo", "arguments": {}})
        )
    body = _decode(resp)
    assert body.get("error", {}).get("code") == -32601
    assert "disabled" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_internal_discover_tools_blocks_disabled():
    """internal_discover_tools (control-plane MCPToolListView path) must block a disabled server."""
    with (
        patch.object(mcp_proxy, "_valid_internal_key", lambda k: True),
        patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value=_DISABLED)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
    ):
        resp = await mcp_proxy.internal_discover_tools(
            _internal_req({"org_slug": "demo", "server_slug": "srv"})
        )
    assert resp.status_code == 403
    assert _decode(resp)["reason"] == "server_disabled"


@pytest.mark.asyncio
async def test_internal_tools_call_allows_enabled_reaches_tool_check():
    """Enabled server proceeds past the server gate to the per-tool disable check."""
    with (
        patch.object(mcp_proxy, "_valid_internal_key", lambda k: True),
        patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value=dict(_ENABLED))),
        patch.object(mcp_proxy, "_apply_fresh_config_overrides", lambda c, *a, **k: c),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "_get_enabled_tools",
                     AsyncMock(return_value={"disabled": {"echo"}})),
    ):
        resp = await mcp_proxy.internal_tools_call(
            _internal_req({"org_slug": "demo", "server_slug": "srv",
                           "tool_name": "echo", "arguments": {}})
        )
    # Reached the per-tool disable check (proof the server gate did NOT fire on an enabled server).
    body = _decode(resp)
    assert body.get("error", {}).get("code") == -32000
    assert "disabled for this server" in body["error"]["message"].lower()


# ── Config-cache version invalidation (finding #17): a scan-version bump must ──
# ── invalidate _get_server_config within one call, not linger for the 120s TTL. ──

def _servers_resp(is_active):
    payload = [{"server_slug": "srv", "transport": "stdio", "url": "", "name": "S",
                "is_active": is_active, "is_exposed_to_agents": True}]
    r = SimpleNamespace(status_code=200)
    r.json = lambda: payload
    return r


class _FakeClient:
    def __init__(self, resp_box):
        self._box = resp_box
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def get(self, *a, **k):
        return self._box[0]


@pytest.mark.asyncio
async def test_get_server_config_version_invalidates(monkeypatch):
    """A scan-version change must refetch config on the next call (not wait out the TTL)."""
    # isolate cache state for the key under test
    mcp_proxy._server_config_cache.pop("demo/srv", None)
    mcp_proxy._server_config_ttl.pop("demo/srv", None)
    mcp_proxy._server_config_ver.pop("demo/srv", None)

    box = [_servers_resp(is_active=True)]
    ver = {"v": "v1"}
    with (
        patch.object(mcp_proxy, "_current_scan_version", AsyncMock(side_effect=lambda *a, **k: ver["v"])),
        patch.object(mcp_proxy.httpx, "AsyncClient", lambda *a, **k: _FakeClient(box)),
    ):
        c1 = await mcp_proxy._get_server_config("demo", "srv")
        assert c1["is_active"] is True

        # backend now reports the server disabled; bump the scan version
        box[0] = _servers_resp(is_active=False)
        ver["v"] = "v2"
        c2 = await mcp_proxy._get_server_config("demo", "srv")
        assert c2["is_active"] is False, "version bump must invalidate cache immediately"

    mcp_proxy._server_config_cache.pop("demo/srv", None)
    mcp_proxy._server_config_ttl.pop("demo/srv", None)
    mcp_proxy._server_config_ver.pop("demo/srv", None)


@pytest.mark.asyncio
async def test_get_server_config_same_version_uses_cache(monkeypatch):
    """No version change within TTL => serve from cache (no needless refetch)."""
    mcp_proxy._server_config_cache.pop("demo/srv", None)
    mcp_proxy._server_config_ttl.pop("demo/srv", None)
    mcp_proxy._server_config_ver.pop("demo/srv", None)

    box = [_servers_resp(is_active=True)]
    with (
        patch.object(mcp_proxy, "_current_scan_version", AsyncMock(return_value="v1")),
        patch.object(mcp_proxy.httpx, "AsyncClient", lambda *a, **k: _FakeClient(box)),
    ):
        c1 = await mcp_proxy._get_server_config("demo", "srv")
        assert c1["is_active"] is True
        # even if backend would now say disabled, same version => cached True served
        box[0] = _servers_resp(is_active=False)
        c2 = await mcp_proxy._get_server_config("demo", "srv")
        assert c2["is_active"] is True

    mcp_proxy._server_config_cache.pop("demo/srv", None)
    mcp_proxy._server_config_ttl.pop("demo/srv", None)
    mcp_proxy._server_config_ver.pop("demo/srv", None)
