"""CHG-0109: audit INBOUND arg redaction (the inbound twin of CHG-0081/CHG-0106).

When a tool call's ARGUMENTS carry PII/IP that the gateway MASKS (scan_action="redact",
not a hard block) before forwarding to the MCP server, the main org_mcp_jsonrpc path folds
that into its per-call ``_was_redacted`` audit event. But the internal, bare-REST, and
external-proxy paths swapped the masked args in SILENTLY — recording NO gateway event — so a
compliance-relevant INPUT redaction (a user's PII masked before it egressed to the MCP
server) was INVISIBLE to the audit/SIEM trail, breaking the "tag inputs → audit" chain.
Now those paths record ``decision="redact", reason="pii_redacted_inbound"``.

These tests drive the two config-reachable paths END-TO-END (real orchestrator redaction via
a ``default_scan_action="redact"`` server). The ext-proxy edit is defense-in-depth parity
(it scans args with enabled_info=None → "tag" → no inbound redaction under current config).
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

_REDACT_CFG = {"default_scan_action": "redact"}
_CLEAN_RESULT = {"jsonrpc": "2.0", "id": 1,
                 "result": {"content": [{"type": "text", "text": "ok, sunny"}]}}
_PII_ARGS = {"note": "email bob.jones@corp.example please"}
_BENIGN_ARGS = {"note": "the weather please"}
_CRED_ARGS = {"config": "AKIAIOSFODNN7EXAMPLE"}


# ── internal_tools_call (chat-pipeline route) ────────────────────────────────

def _internal_req(args):
    return SimpleNamespace(
        headers={"X-Gateway-Internal-Key": "ok"},
        json=AsyncMock(return_value={
            "org_slug": "demo", "server_slug": "srv", "tool_name": "fetch",
            "arguments": args}))


async def _drive_internal(args, *, capture=None):
    audit = AsyncMock()

    async def _fwd(transport, cfg, org, srv, call_body, jr, mid, *, correlation_id="", forwarded_auth=None, **_kw):
        if capture is not None:
            capture["args"] = call_body["params"]["arguments"]
        return JSONResponse(content=_CLEAN_RESULT, status_code=200)

    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_is_sandbox_routed", return_value=True),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "stdio", "command": "x"})),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=_REDACT_CFG)),
        patch.object(mcp_proxy, "_record_gateway_event", audit),
        patch.object(mcp_proxy, "_adapter_forward", AsyncMock(side_effect=_fwd)),
    ):
        await mcp_proxy.internal_tools_call(_internal_req(args))
    return [(c.kwargs.get("decision"), c.kwargs.get("reason")) for c in audit.call_args_list]


@pytest.mark.asyncio
async def test_internal_inbound_redaction_is_audited():
    cap = {}
    events = await _drive_internal(_PII_ARGS, capture=cap)
    # The email was actually masked before forwarding (real orchestrator redaction)…
    assert "bob.jones@corp.example" not in json.dumps(cap["args"])
    assert "b***@c***.example" in json.dumps(cap["args"])
    # …and the inbound redaction is now audited.
    assert ("redact", "pii_redacted_inbound") in events


@pytest.mark.asyncio
async def test_internal_benign_args_not_audited():
    # No redaction → no inbound audit noise (findings-only audit path).
    assert await _drive_internal(_BENIGN_ARGS) == []


@pytest.mark.asyncio
async def test_internal_credential_redacted_under_redact_posture():
    # STRICT OPERATOR CONTROL (2026-07-22): redact means redact. A credential in
    # inbound args is MASKED under the operator's ``redact`` posture — NOT
    # force-blocked. The credential force-block escalation (redact -> block) is now
    # off by default; the operator selects ``block`` to hard-block a credential.
    events = await _drive_internal(_CRED_ARGS)
    decisions = [d for d, _ in events]
    assert "block" not in decisions      # no force-block escalation under redact
    assert "redact" in decisions         # the credential was masked in place


# ── org_mcp_tool_call (bare REST route) ──────────────────────────────────────

def _auth():
    return AuthContext(key_hash="h" * 64, payload={
        "key_id": "k1", "user_id": 1, "project_id": "p1", "org_slug": "demo",
        "mcp_allowed_tools": [], "mcp_max_tool_calls": 0})


def _rest_req(body_obj):
    req = SimpleNamespace(state=SimpleNamespace(auth_context=_auth()))
    req.body = AsyncMock(return_value=json.dumps(body_obj).encode())
    return req


def _backend_resp(json_body):
    r = AsyncMock()
    r.status_code = 200
    r.json = lambda: json_body
    r.text = json.dumps(json_body)
    r.raise_for_status = lambda: None
    return r


def _fake_client(resp):
    c = AsyncMock()
    c.post = AsyncMock(return_value=resp)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


async def _drive_rest(args):
    audit = AsyncMock()
    backend = _backend_resp({"result": [{"type": "text", "text": "ok, sunny"}]})
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=_REDACT_CFG)),
        patch.object(mcp_proxy, "_record_gateway_event", audit),
        patch.object(mcp_proxy, "_incr_tool_call_count", AsyncMock(return_value=1)),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_client(backend)),
    ):
        await mcp_proxy.org_mcp_tool_call("demo", "srv",
                                          _rest_req({"name": "fetch", "arguments": args}))
    return [(c.kwargs.get("decision"), c.kwargs.get("reason")) for c in audit.call_args_list]


@pytest.mark.asyncio
async def test_rest_inbound_redaction_is_audited():
    events = await _drive_rest(_PII_ARGS)
    assert ("redact", "pii_redacted_inbound") in events


@pytest.mark.asyncio
async def test_rest_benign_args_no_inbound_redact_audit():
    events = await _drive_rest(_BENIGN_ARGS)
    assert ("redact", "pii_redacted_inbound") not in events


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
