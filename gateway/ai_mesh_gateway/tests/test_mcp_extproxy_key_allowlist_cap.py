"""AU3-02 (lifecycle red-team wf_a29f8f21): the transparent ext-proxy route must
enforce the per-key tool allowlist + per-key tool-call cap, parity with the org routes.
Previously it enforced NEITHER, so a key restricted to mcp_allowed_tools=['safe'] could
call any tool and a key with mcp_max_tool_calls=N got unlimited calls via
/v1/mcp/ext-proxy/<host>.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import mcp_proxy
from middleware import AuthContext

_EXT_HOST = next(iter(mcp_proxy._ALLOWED_MCP_DOMAINS))  # an allowlisted proxy domain


def _auth(*, allowed=None, cap=0):
    return AuthContext(key_hash="h" * 64, payload={
        "key_id": "k1", "user_id": 1, "org_slug": "demo",
        "mcp_allowed_tools": list(allowed or []), "mcp_max_tool_calls": cap,
    })


def _ext_request(body_obj, auth):
    req = SimpleNamespace()
    req.method = "POST"
    req.headers = {"content-type": "application/json"}
    req.query_params = {}
    req.body = AsyncMock(return_value=json.dumps(body_obj).encode())
    req.state = SimpleNamespace(auth_context=auth)
    return req


def _aiter(payload):
    async def _gen():
        yield payload
    return _gen


def _upstream_ok():
    r = AsyncMock()
    r.status_code = 200
    r.headers = {"content-type": "application/json"}
    _p = json.dumps({"jsonrpc": "2.0", "id": 1,
                     "result": {"content": [{"type": "text", "text": "ok"}]}}).encode()
    r.json = lambda: json.loads(_p)
    r.aread = AsyncMock(return_value=_p)
    r.aiter_bytes = _aiter(_p)
    r.aclose = AsyncMock()
    return r


def _client(resp):
    c = AsyncMock()
    c.build_request = lambda **_k: SimpleNamespace()
    c.send = AsyncMock(return_value=resp)
    c.aclose = AsyncMock()
    return c


def _call(name):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": {"q": "hi"}}}


def _decode(resp):
    return json.loads(bytes(resp.body))


@pytest.fixture(autouse=True)
def _guards(monkeypatch):
    monkeypatch.setattr(mcp_proxy, "is_safe_outbound_url", lambda *_a, **_k: (True, "ok"))
    monkeypatch.setattr(mcp_proxy, "_record_gateway_event", AsyncMock())


@pytest.mark.asyncio
async def test_ext_blocks_tool_not_in_key_allowlist():
    client = _client(_upstream_ok())
    req = _ext_request(_call("create_or_update_file"), _auth(allowed=["read_only"]))
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    data = _decode(resp)
    assert "error" in data and "not allowed" in data["error"]["message"].lower()
    client.send.assert_not_awaited()  # blocked BEFORE the upstream is reached


@pytest.mark.asyncio
async def test_ext_blocks_over_tool_call_cap():
    client = _client(_upstream_ok())
    req = _ext_request(_call("read_only"), _auth(allowed=["read_only"], cap=1))
    with (
        patch.object(mcp_proxy, "_incr_tool_call_count", AsyncMock(return_value=2)),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    data = _decode(resp)
    assert "error" in data and "limit exceeded" in data["error"]["message"].lower()
    client.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_ext_allowed_tool_within_cap_passes():
    client = _client(_upstream_ok())
    req = _ext_request(_call("read_only"), _auth(allowed=["read_only"], cap=5))
    with (
        patch.object(mcp_proxy, "_incr_tool_call_count", AsyncMock(return_value=1)),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200
    client.send.assert_awaited()  # allowed tool reached the upstream


@pytest.mark.asyncio
async def test_ext_empty_allowlist_allows_any_tool():
    client = _client(_upstream_ok())
    req = _ext_request(_call("anything"), _auth(allowed=[], cap=0))
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200
    client.send.assert_awaited()
