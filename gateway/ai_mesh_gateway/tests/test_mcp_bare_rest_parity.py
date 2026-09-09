"""CHG-0082: two bare-REST parity gaps vs org_mcp_jsonrpc / CHG-0077.

(A) `org_mcp_tool_call` audited a result BLOCK but swapped in a REDACTED result
    SILENTLY (no `_record_gateway_event`) — so a secret/PII/IP masked on the primary
    bare-REST tool-call path was invisible to audit/SIEM.
(B) the REST `org_mcp_tools_list` endpoint (GET .../tools) FILTERED tools but did NOT
    scan the tool descriptions, while the JSON-RPC tools/list already scans them
    (CHG-0077) — a tool-poisoning / secret/PII/IP metadata-leak parity gap.
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

_SECRET = "sk-ant-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234"

# STRICT OPERATOR CONTROL (2026-07-21): result redaction happens only under an
# operator-selected ENFORCING posture. These tests used to stub ``_get_enabled_tools``
# with ``None``, which resolves to the observe-only "tag" posture (detect + tag, never
# mutate) — asserting masking there contradicted the product rule. The org here has
# explicitly selected ``redact``.
_ENFORCING = {"default_scan_action": "redact"}


# __PDD_PRESET_FIXTURE__

# policy-driven-detection cutover (task 9): the MCP built-in default detectors (the Tier-1
# PRESET pass) are now EFFECTIVE-DEFAULT OFF (mcp_scan_orchestrator._mcp_default_detection_enabled)
# so a zero-enabled-policy org is passthrough. This module exercises the RETAINED preset
# DETECTION MACHINERY (redaction / fail-closed byte-truth / exfil-defang / authz / audit), which
# stays reachable via the explicit opt-in env. Enable it for this module so those invariants are
# still tested. The default-OFF (Zero_Policy_State passthrough) contract is asserted by the
# dedicated test_policy_driven_* modules, not weakened here.
import os as _os_pdd


@pytest.fixture(autouse=True)
def _enable_builtin_mcp_presets(monkeypatch):
    monkeypatch.setenv("GATEWAY_MCP_DEFAULT_DETECTION", "true")
    monkeypatch.setenv("GATEWAY_MCP_REDACT_RESULT_ON_DETECT", "true")
    yield


def _auth():
    return AuthContext(key_hash="h" * 64, payload={
        "key_id": "k1", "user_id": 1, "project_id": "p1", "org_slug": "demo",
        "mcp_allowed_tools": [], "mcp_max_tool_calls": 0,
    })


def _http_resp(json_body, status=200):
    r = AsyncMock()
    r.status_code = status
    r.json = lambda: json_body
    r.text = json.dumps(json_body)
    r.headers = {"content-type": "application/json"}
    r.raise_for_status = lambda: None
    return r


def _fake_post_client(resp):
    c = AsyncMock()
    c.post = AsyncMock(return_value=resp)
    c.get = AsyncMock(return_value=resp)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


def _rest_request(body_obj):
    req = SimpleNamespace(state=SimpleNamespace(auth_context=_auth()))
    req.body = AsyncMock(return_value=json.dumps(body_obj).encode())
    req.headers = {}
    req.query_params = {}
    return req


# ── Gap A: bare-REST tool-call result REDACT is now audited ──
@pytest.mark.asyncio
async def test_rest_tool_call_redact_is_audited():
    req = _rest_request({"name": "fetch", "arguments": {"q": "hi"}})
    backend = _http_resp({"result": [{"type": "text", "text": f"the api key is {_SECRET}"}]})
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=_ENFORCING)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()) as rec,
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_post_client(backend)),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 200
    assert _SECRET not in json.dumps(json.loads(resp.body.decode()))  # masked
    # the redaction must have produced an audit event with decision=redact
    decisions = [c.kwargs.get("decision") for c in rec.await_args_list]
    assert "redact" in decisions, f"redact not audited; decisions={decisions}"


# ── Gap B: REST tools-list endpoint now scans + audits descriptions ──
async def _run_tools_list(backend_tools):
    req = SimpleNamespace(state=SimpleNamespace(auth_context=_auth()), headers={}, query_params={})
    backend = _http_resp(backend_tools)
    with (
        patch.object(mcp_proxy, "_get_auth_context", return_value=_auth()),
        patch.object(mcp_proxy, "_audit_and_return_scope_error", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=_ENFORCING)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()) as rec,
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_post_client(backend)),
    ):
        resp = await mcp_proxy.org_mcp_tools_list("demo", "srv", req)
    return resp, rec


@pytest.mark.asyncio
async def test_rest_tools_list_scans_and_audits_descriptions():
    resp, rec = await _run_tools_list(
        [{"tool_name": "add", "description": f"Internal gw 10.1.2.3 key {_SECRET}"}])
    blob = json.dumps(json.loads(resp.body.decode()))
    assert _SECRET not in blob and "10.1.2.3" not in blob, "leak in REST tools-list description"
    decisions = [c.kwargs.get("decision") for c in rec.await_args_list]
    assert "redact" in decisions


@pytest.mark.asyncio
async def test_rest_tools_list_benign_not_audited():
    resp, rec = await _run_tools_list([{"tool_name": "ok", "description": "Fetches weather data"}])
    assert resp.status_code == 200
    assert rec.await_count == 0, "a clean REST tools-list must not emit audit noise"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
