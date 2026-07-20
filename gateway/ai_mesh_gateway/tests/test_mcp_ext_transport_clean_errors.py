"""Item 04 — the external transport path (ext_mcp_proxy) returns CLEAN errors:
a transport failure (DNS / connection-refused) → a branded message that NEVER
leaks the internal hostname or the raw exception; an upstream 401/403 → a clean
're-authenticate' prompt, not the raw upstream auth body.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402

_EXT_HOST = next(iter(mcp_proxy._ALLOWED_MCP_DOMAINS))


@pytest.fixture(autouse=True)
def _ssrf_allow(monkeypatch):
    # keep hermetic — no real DNS in the SSRF guard
    monkeypatch.setattr(mcp_proxy, "is_safe_outbound_url", lambda *_a, **_k: (True, "ok"))


def _ext_request(body_obj):
    req = SimpleNamespace()
    req.method = "POST"
    req.headers = {"content-type": "application/json"}
    req.query_params = {}
    req.body = AsyncMock(return_value=json.dumps(body_obj).encode())
    return req


def _aiter_of(payload: bytes):
    async def _gen():
        yield payload
    return _gen


def _send_resp(json_body, *, status=200, content_type="application/json"):
    r = AsyncMock()
    r.status_code = status
    r.headers = {"content-type": content_type}
    r.json = lambda: json_body
    p = json.dumps(json_body).encode()
    r.aread = AsyncMock(return_value=p)
    r.aiter_bytes = _aiter_of(p)
    r.aclose = AsyncMock()
    return r


def _client(send_resp=None, *, raise_exc=None):
    c = AsyncMock()
    c.build_request = lambda **_k: SimpleNamespace()
    c.send = AsyncMock(side_effect=raise_exc) if raise_exc else AsyncMock(return_value=send_resp)
    c.aclose = AsyncMock()
    return c


def _decode(resp):
    return json.loads(bytes(resp.body))


_REQ = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "fetch", "arguments": {"q": "hi"}}}


async def _drive(client):
    with patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()), \
         patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        return await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", _ext_request(_REQ))


@pytest.mark.asyncio
async def test_ext_dns_failure_clean_no_host_no_exc():
    exc = httpx.ConnectError("[Errno -2] Name or service not known: internal-host.corp.local")
    resp = await _drive(_client(raise_exc=exc))
    assert resp.status_code == 502
    data = _decode(resp)
    assert data["code"] == "MCP_DNS_FAILURE"
    assert "could not reach" in data["error"].lower()
    blob = json.dumps(data)
    for tok in ("internal-host", "corp.local", "Errno", "Name or service", _EXT_HOST):
        assert tok not in blob, f"leak: {tok!r}"
    assert "detail" not in data  # the old raw-detail field is gone


@pytest.mark.asyncio
async def test_ext_connection_refused_clean():
    exc = httpx.ConnectError("[Errno 111] Connection refused: 10.9.8.7:443")
    resp = await _drive(_client(raise_exc=exc))
    data = _decode(resp)
    assert data["code"] == "MCP_CONNECTION_REFUSED"
    blob = json.dumps(data)
    assert "10.9.8.7" not in blob and "Errno" not in blob


@pytest.mark.asyncio
async def test_ext_upstream_401_is_clean_reauth_prompt():
    # upstream 401 body carries a token hint that must NOT be echoed
    resp = await _drive(_client(_send_resp(
        {"error": "invalid_token", "hint": "bearer sk-live-SECRETHINT expired"}, status=401)))
    assert resp.status_code == 401
    data = _decode(resp)
    assert data["code"] == "MCP_AUTH_FAILED"
    assert "re-auth" in data["error"].lower()
    blob = json.dumps(data)
    assert "sk-live-SECRETHINT" not in blob and "invalid_token" not in blob
