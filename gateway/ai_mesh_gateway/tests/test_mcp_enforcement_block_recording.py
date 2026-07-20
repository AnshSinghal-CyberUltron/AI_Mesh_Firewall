"""CP24: end-to-end (in-process) proof that the gateway's tool-call handler records
a control-plane ENFORCEMENT denial (HTTP 403 tool_not_registered / policy block) as
``decision="block"`` — not ``decision="error"`` — while a genuine backend fault
(HTTP 500) stays ``decision="error"``. Drives the REAL ``org_mcp_tool_call`` handler
over the direct-httpx path with the backend network mocked (no sockets), so the audit
counters provably reflect real enforced reality (the CP22 anomaly fix, CP23).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_proxy  # noqa: E402
from middleware import AuthContext  # noqa: E402


@pytest.fixture(autouse=True)
def _force_direct_http_path(monkeypatch):
    # Exercise the legacy direct-control httpx path (where the CP23 classifier lives);
    # the sandbox-routed path is covered elsewhere.
    monkeypatch.setenv("MCP_HTTP_VIA_SANDBOX", "0")


def _auth(org_slug="demo"):
    return AuthContext(
        key_hash="h" * 64,
        payload={
            "key_id": "k1", "user_id": 1, "project_id": "p1",
            "org_slug": org_slug, "mcp_allowed_tools": [], "mcp_max_tool_calls": 0,
        },
    )


def _jsonrpc_request(auth=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "fetch", "arguments": {"q": "hello"}}}
    req = SimpleNamespace(state=SimpleNamespace(auth_context=auth or _auth()))
    req.json = AsyncMock(return_value=body)
    req.headers = {}
    return req


def _http_resp(json_body, *, status=200):
    r = AsyncMock()
    r.status_code = status
    r.json = lambda: json_body
    r.text = json.dumps(json_body)
    r.headers = {"content-type": "application/json"}
    r.raise_for_status = lambda: None
    return r


def _fake_client(resp):
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _recorded_decisions(rec: AsyncMock):
    """(decision, reason, enforced_at) for every _record_gateway_event call."""
    out = []
    for c in rec.await_args_list:
        kw = c.kwargs
        out.append((kw.get("decision"), kw.get("reason"), (kw.get("metadata") or {}).get("enforced_at")))
    return out


async def _drive(backend_resp):
    """Drive the real org_mcp_jsonrpc handler down the direct-control path (the
    site of the CP23 fix), with the control /tools/call backend mocked."""
    rec = AsyncMock()
    req = _jsonrpc_request()
    srv_config = {"transport": "streamable-http", "url": "https://safe.example.com/mcp"}
    with (
        patch.object(mcp_proxy, "_enforce_mcp_org_rate_limits", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value=srv_config)),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", rec),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_client(backend_resp)),
    ):
        resp = await mcp_proxy.org_mcp_jsonrpc("demo", "srv", req)
    return resp, _recorded_decisions(rec)


@pytest.mark.asyncio
async def test_backend_403_tool_not_registered_recorded_as_block():
    backend = _http_resp(
        {"error": "Tool is not registered for this server", "reason": "tool_not_registered"},
        status=403,
    )
    _resp, decisions = await _drive(backend)
    # Exactly one enforcement event, recorded as a BLOCK with the real reason.
    assert ("block", "tool_not_registered", "backend") in decisions, decisions
    assert not any(d == "error" for d, _, _ in decisions), decisions


@pytest.mark.asyncio
async def test_backend_policy_block_recorded_as_block():
    backend = _http_resp(
        {"error": "Tool call blocked by policy", "reason": "credentials not allowed"},
        status=403,
    )
    _resp, decisions = await _drive(backend)
    assert any(d == "block" and r == "credentials not allowed" for d, r, _ in decisions), decisions


@pytest.mark.asyncio
async def test_backend_500_stays_error():
    backend = _http_resp({"error": "internal"}, status=500)
    _resp, decisions = await _drive(backend)
    assert ("error", "backend_error_http_500", "gateway") in decisions, decisions
    assert not any(d == "block" for d, _, _ in decisions), decisions
