"""S12 — MCP JSON-RPC path rate limiting (TPM + burst/RPM).

Proves org_mcp_jsonrpc applies the shared rate-limit helpers and maps HTTP 429
responses to the MCP JSON-RPC error envelope (HTTP 200), not a bare 429 body.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.responses import JSONResponse

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402
from middleware import AuthContext  # noqa: E402


def _make_request(auth):
    return SimpleNamespace(state=SimpleNamespace(auth_context=auth))


def _auth(org_slug="demo"):
    return AuthContext(
        key_hash="h" * 64,
        payload={
            "key_id": "k1",
            "user_id": 1,
            "project_id": "p1",
            "org_slug": org_slug,
        },
    )


def _decode(resp):
    return json.loads(resp.body.decode("utf-8"))


def _tpm_429():
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limited",
            "scope": "org",
            "message": "Org TPM ceiling exceeded (10500/10000).",
            "code": "org_rate_limit_exceeded",
        },
        headers={"Retry-After": "60"},
    )


def _burst_429():
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limited",
            "message": "Burst limit exceeded (151/150 req/s).",
            "code": "burst_limit_exceeded",
        },
        headers={"Retry-After": "1"},
    )


@pytest.mark.asyncio
async def test_rate_limit_helper_maps_429_to_jsonrpc_envelope():
    resp = mcp_proxy._rate_limit_response_to_jsonrpc(
        _tpm_429(), jsonrpc="2.0", msg_id=42,
    )
    assert resp.status_code == 200
    data = _decode(resp)
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 42
    assert data["error"]["code"] == mcp_proxy._JSONRPC_RATE_LIMIT_CODE
    assert "TPM ceiling exceeded" in data["error"]["message"]
    assert data["error"]["data"]["code"] == "org_rate_limit_exceeded"
    assert resp.headers.get("Retry-After") == "60"


@pytest.mark.asyncio
async def test_org_mcp_jsonrpc_tpm_exceeded_returns_jsonrpc_error():
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {}}
    req.json = AsyncMock(return_value=body)

    with patch.object(mcp_proxy, "_validate_org_scope", return_value=None), patch(
        "main._enforce_org_tpm_rate_limit",
        new_callable=AsyncMock,
        return_value=_tpm_429(),
    ), patch(
        "main._enforce_org_burst_rpm",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await mcp_proxy.org_mcp_jsonrpc("demo", "srv", req)

    assert resp.status_code == 200
    data = _decode(resp)
    assert data["id"] == 7
    assert "error" in data
    assert data["error"]["code"] == mcp_proxy._JSONRPC_RATE_LIMIT_CODE
    assert "TPM ceiling exceeded" in data["error"]["message"]


@pytest.mark.asyncio
async def test_org_mcp_jsonrpc_burst_exceeded_returns_jsonrpc_error():
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": "req-1", "method": "tools/list", "params": {}}
    req.json = AsyncMock(return_value=body)

    with patch.object(mcp_proxy, "_validate_org_scope", return_value=None), patch(
        "main._enforce_org_tpm_rate_limit",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "main._enforce_org_burst_rpm",
        new_callable=AsyncMock,
        return_value=_burst_429(),
    ):
        resp = await mcp_proxy.org_mcp_jsonrpc("demo", "srv", req)

    assert resp.status_code == 200
    data = _decode(resp)
    assert data["id"] == "req-1"
    assert data["error"]["data"]["code"] == "burst_limit_exceeded"
    assert "Burst limit exceeded" in data["error"]["message"]


@pytest.mark.asyncio
async def test_org_mcp_jsonrpc_under_limit_proceeds_to_initialize():
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    req.json = AsyncMock(return_value=body)

    with patch.object(mcp_proxy, "_validate_org_scope", return_value=None), patch(
        "main._enforce_org_tpm_rate_limit",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "main._enforce_org_burst_rpm",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await mcp_proxy.org_mcp_jsonrpc("demo", "srv", req)

    assert resp.status_code == 200
    data = _decode(resp)
    assert "result" in data
    assert data["result"]["protocolVersion"] == "2024-11-05"
    assert "error" not in data


# ── CHG-0031: the bare REST route org_mcp_tool_call now enforces per-org rate
# limits too (it previously had the per-key tool-call CAP but NOT the per-org
# TPM/burst/RPM limit that org_mcp_jsonrpc applied). It returns a PLAIN 429, not
# the JSON-RPC-200 envelope the JSON-RPC route uses.


@pytest.mark.asyncio
async def test_mcp_org_rate_limit_raw_returns_plain_429_and_none_under_limit():
    with patch("main._enforce_org_tpm_rate_limit", new_callable=AsyncMock, return_value=_tpm_429()), \
         patch("main._enforce_org_burst_rpm", new_callable=AsyncMock, return_value=None):
        resp = await mcp_proxy._mcp_org_rate_limit_raw(_auth())
    assert resp.status_code == 429                      # plain 429, NOT a 200 envelope
    assert _decode(resp)["code"] == "org_rate_limit_exceeded"

    with patch("main._enforce_org_tpm_rate_limit", new_callable=AsyncMock, return_value=None), \
         patch("main._enforce_org_burst_rpm", new_callable=AsyncMock, return_value=None):
        assert await mcp_proxy._mcp_org_rate_limit_raw(_auth()) is None


@pytest.mark.asyncio
async def test_org_mcp_tool_call_tpm_exceeded_returns_plain_429():
    req = _make_request(_auth())
    req.body = AsyncMock(return_value=b'{"name": "echo", "arguments": {}}')
    with patch.object(mcp_proxy, "_validate_org_scope", return_value=None), \
         patch("main._enforce_org_tpm_rate_limit", new_callable=AsyncMock, return_value=_tpm_429()), \
         patch("main._enforce_org_burst_rpm", new_callable=AsyncMock, return_value=None):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 429                      # bare REST 429, not JSON-RPC 200
    assert _decode(resp)["code"] == "org_rate_limit_exceeded"


@pytest.mark.asyncio
async def test_org_mcp_tool_call_burst_exceeded_returns_plain_429():
    req = _make_request(_auth())
    req.body = AsyncMock(return_value=b'{"name": "echo", "arguments": {}}')
    with patch.object(mcp_proxy, "_validate_org_scope", return_value=None), \
         patch("main._enforce_org_tpm_rate_limit", new_callable=AsyncMock, return_value=None), \
         patch("main._enforce_org_burst_rpm", new_callable=AsyncMock, return_value=_burst_429()):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 429
    assert _decode(resp)["code"] == "burst_limit_exceeded"
