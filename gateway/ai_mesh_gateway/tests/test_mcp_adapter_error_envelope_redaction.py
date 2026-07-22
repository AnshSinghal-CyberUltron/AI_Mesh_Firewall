"""CHG-0091 — leak on the stdio/websocket ADAPTER tools/call path: a bare JSON-RPC
ERROR envelope (``{"jsonrpc","id","error":{...}}`` — NO ``result`` key, the standard
response an MCP upstream returns on tool failure) whose ``error.message`` carries a
secret / internal IP was DETECTED + TAGGED but egressed RAW.

Root cause: ``org_mcp_jsonrpc`` scans ``_scan_target = payload.get("result") if
"result" in payload else payload`` — so an error envelope IS scanned whole — but all
three output swap branches (redact, redaction-floor condition, floor swap) were gated
on ``"result" in payload``. For a bare error envelope the redacted output was computed
then DISCARDED and the raw error was returned. Under the DEFAULT "tag" posture the
first scan only detects (does not mask), so the floor is the operative masker — and its
gate was exactly the one that excluded error envelopes.

Fix (CHG-0091): drop the ``"result" in payload`` gate from the floor condition and make
both redact-swap branches write the redacted output back to the WHOLE envelope when
there is no ``result`` key (and keep the audited ``reason`` in sync with the masked
message). Symmetric with the streamable-http path, which already swaps unconditionally.

POSTURE NOTE (2026-07-21): enforcement is STRICTLY what the operator selected for the
org. ``tag``/``monitor`` are OBSERVE-ONLY — the envelope is detected and tagged but
NEVER mutated — and the static hardening floors (including this error-envelope masker)
fire only under an operator-selected ENFORCING posture (``redact``/``block``). The
masking tests below therefore select ``{"default_scan_action": "redact"}`` explicitly;
the observe-only tests assert the raw envelope survives, which is the correct contract.

These tests drive the REAL ``org_mcp_jsonrpc`` handler end-to-end (only the transport
boundary ``_adapter_forward`` / config / audit are faked) and assert on the EGRESS
BYTES — the only source of truth.

Run:
    cd .../gateway && .venv/bin/python -m pytest \
        ai_mesh_gateway/tests/test_mcp_adapter_error_envelope_redaction.py -q
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

# A secret + internal-network IP living inside a tool-FAILURE error message — the
# realistic shape: an upstream tool echoing the connection string it failed on.
_RAW_SECRET = "ghp_REALLOOKINGSECRET1234ABCD"
_RAW_IP = "10.0.0.5"
# A secret that reliably matches a STANDALONE detector (not only inside a connection
# string): the canonical AWS example access key → aws_access_key, tag SECRET.
_RAW_SECRET_STANDALONE = "AKIAIOSFODNN7EXAMPLE"
_LEAKY_ERR_MSG = (
    f"connect failed: postgres://svc:{_RAW_SECRET}@{_RAW_IP}:5432/prod timed out"
)
_BENIGN_ERR_MSG = "tool 'echo' not found on this server"


def _make_request(auth):
    return SimpleNamespace(state=SimpleNamespace(auth_context=auth))


def _auth(org_slug="demo"):
    payload = {
        "key_id": "k1", "user_id": 1, "project_id": "p1", "org_slug": org_slug,
        "mcp_allowed_tools": [], "mcp_max_tool_calls": 0,
    }
    return AuthContext(key_hash="h" * 64, payload=payload)


def _decode(resp):
    return json.loads(resp.body.decode("utf-8"))


async def _run_adapter_error(request, body, *, enabled_info, error_obj):  # noqa: D401
    """Drive org_mcp_jsonrpc on the stdio adapter path where the upstream returns a
    BARE JSON-RPC ERROR envelope (no ``result`` key)."""
    from fastapi.responses import JSONResponse
    raw = JSONResponse(content={
        "jsonrpc": "2.0", "id": body["id"], "error": error_obj,
    }, status_code=200)
    request.json = AsyncMock(return_value=body)
    with (
        patch.object(mcp_proxy, "_validate_org_scope", return_value=None),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "stdio", "command": "x"})),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=enabled_info)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "_incr_tool_call_count", AsyncMock(return_value=1)),
        patch.object(mcp_proxy, "_adapter_forward", AsyncMock(return_value=raw)),
    ):
        return await mcp_proxy.org_mcp_jsonrpc("demo", "srv", request)


def _call_body(msg_id):
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
            "params": {"name": "echo", "arguments": {"q": "hi"}}}


@pytest.mark.asyncio
async def test_error_envelope_secret_and_ip_redacted_under_redact_posture():
    """The bug: a secret + internal IP inside error.message must be MASKED on egress,
    not returned raw, on the stdio adapter path.

    PREMISE REWRITTEN (was ``test_error_envelope_secret_and_ip_redacted_under_tag_default``):
    it used to drive this with NO operator selection and assert masking anyway, i.e. it
    asserted that the "tag" default silently enforced redaction. That contradicts the
    product rule — ``tag`` is OBSERVE-ONLY and nothing the operator did not select is
    enforced. The security property under test (the error envelope is masked, not
    forwarded raw) is unchanged and just as strong; only the posture is now explicit."""
    req = _make_request(_auth())
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled", return_value=True):
        resp = await _run_adapter_error(
            req, _call_body(31), enabled_info={"default_scan_action": "redact"},
            error_obj={"code": -32000, "message": _LEAKY_ERR_MSG},
        )
    decoded = _decode(resp)
    blob = json.dumps(decoded)
    assert _RAW_SECRET not in blob, "secret in error.message egressed RAW"
    assert _RAW_IP not in blob, "internal IP in error.message egressed RAW"
    # the envelope is still a well-formed JSON-RPC error (structure preserved by redaction)
    assert decoded.get("id") == 31
    assert isinstance(decoded.get("error"), dict)


@pytest.mark.asyncio
async def test_error_envelope_benign_passes_through_unchanged():
    """Regression guard: a benign tool-failure error (no secret/PII) is returned
    unchanged — the floor must not corrupt or block a clean error."""
    req = _make_request(_auth())
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled", return_value=True):
        resp = await _run_adapter_error(
            req, _call_body(32), enabled_info=None,
            error_obj={"code": -32601, "message": _BENIGN_ERR_MSG},
        )
    decoded = _decode(resp)
    assert decoded["error"]["message"] == _BENIGN_ERR_MSG
    assert "***" not in json.dumps(decoded)


@pytest.mark.asyncio
async def test_error_envelope_no_operator_selection_leaves_raw():
    """Negative control: with NO enforcing posture selected by the operator the error
    envelope is NOT mutated, even with GATEWAY_MCP_REDACT_RESULT_ON_DETECT ON.

    PREMISE REWRITTEN (was ``test_error_envelope_flag_disabled_leaves_raw``): it turned
    the env flag OFF while leaving the posture unselected, to "prove the floor is the
    masker". Under the product rule the floor is gated on the OPERATOR-selected posture,
    so with an enforcing posture selected the envelope is masked whatever that env flag
    says, and with nothing selected it is never masked — the flag is not the axis that
    decides. This version tests the axis that actually decides, and keeps the same
    non-vacuity role: it proves the masking assertions above come from enforcement being
    switched on, not from some unrelated path that always mutates."""
    req = _make_request(_auth())
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled", return_value=True):
        resp = await _run_adapter_error(
            req, _call_body(33), enabled_info=None,
            error_obj={"code": -32000, "message": _LEAKY_ERR_MSG},
        )
    blob = json.dumps(_decode(resp))
    assert _RAW_SECRET in blob  # nothing selected → nothing enforced → raw passes through


@pytest.mark.asyncio
async def test_error_envelope_explicit_monitor_does_not_force_redact():
    """An explicit per-tool "monitor" action is observe-only and still wins on the
    error-envelope path (same carve-out as the result path)."""
    req = _make_request(_auth())
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled", return_value=True):
        resp = await _run_adapter_error(
            req, _call_body(34),
            enabled_info={"tool_scan_actions": {"echo": "monitor"}},
            error_obj={"code": -32000, "message": _LEAKY_ERR_MSG},
        )
    blob = json.dumps(_decode(resp))
    assert _RAW_SECRET in blob  # monitor wins → observe-only → raw passes through


@pytest.mark.asyncio
async def test_error_envelope_unmaskable_survivor_fails_closed():
    """An error message mixing a maskable secret/IP with an UNMASKABLE private file
    path fails CLOSED (block) on the error-envelope path — never a raw forward."""
    req = _make_request(_auth())
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled", return_value=True):
        resp = await _run_adapter_error(
            req, _call_body(35), enabled_info={"default_scan_action": "redact"},
            error_obj={"code": -32000,
                       "message": f"box {_RAW_IP} served key /home/bob/.ssh/id_rsa"},
        )
    decoded = _decode(resp)
    blob = json.dumps(decoded)
    assert _RAW_IP not in blob
    assert "/home/bob/.ssh/id_rsa" not in blob
    # fail-closed => a [BLOCKED] result envelope, isError set
    assert "[BLOCKED]" in blob
    assert decoded.get("result", {}).get("isError") is True


# ── CHG-0092: tools/LIST adapter error-envelope twin ─────────────────────────
# The tools/list adapter fall-through (org_mcp_jsonrpc, the `return adapter_resp`
# after the tools-shaped branch) returned RAW for any non-tools-shaped payload —
# including a bare JSON-RPC error envelope (an auth-failure tools/list error can
# echo a token/URL). Now it is scanned through the result floor too.


async def _run_adapter_toolslist_error(request, body, *, enabled_info, error_obj):
    """Drive org_mcp_jsonrpc on the stdio adapter tools/list path where the upstream
    returns a BARE JSON-RPC ERROR envelope (no result/tools)."""
    from fastapi.responses import JSONResponse
    raw = JSONResponse(content={
        "jsonrpc": "2.0", "id": body["id"], "error": error_obj,
    }, status_code=200)
    request.json = AsyncMock(return_value=body)
    with (
        patch.object(mcp_proxy, "_validate_org_scope", return_value=None),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "stdio", "command": "x"})),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=enabled_info)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "_adapter_forward", AsyncMock(return_value=raw)),
    ):
        return await mcp_proxy.org_mcp_jsonrpc("demo", "srv", request)


def _list_body(msg_id):
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/list", "params": {}}


@pytest.mark.asyncio
async def test_toolslist_error_envelope_secret_and_ip_redacted():
    """A token + internal IP echoed in a tools/list ERROR (e.g. auth failure) is MASKED
    on the stdio adapter path, not returned raw."""
    req = _make_request(_auth())
    resp = await _run_adapter_toolslist_error(
        req, _list_body(51), enabled_info={"default_scan_action": "redact"},
        error_obj={"code": -32001, "message": f"auth failed for key {_RAW_SECRET_STANDALONE} at {_RAW_IP}"},
    )
    blob = json.dumps(_decode(resp))
    assert _RAW_SECRET_STANDALONE not in blob, "secret in tools/list error egressed RAW"
    assert _RAW_IP not in blob, "internal IP in tools/list error egressed RAW"


@pytest.mark.asyncio
async def test_toolslist_error_envelope_benign_unchanged():
    """A benign tools/list error is returned unchanged — no corruption or withholding."""
    req = _make_request(_auth())
    resp = await _run_adapter_toolslist_error(
        req, _list_body(52), enabled_info=None,
        error_obj={"code": -32000, "message": "server temporarily unavailable"},
    )
    decoded = _decode(resp)
    assert decoded["error"]["message"] == "server temporarily unavailable"
    assert "***" not in json.dumps(decoded)


@pytest.mark.asyncio
async def test_toolslist_tools_shaped_still_scanned_not_regressed():
    """Guard: a normal tools-shaped tools/list response is still scanned via the
    existing metadata path (CHG-0077) — the CHG-0092 fall-through must not shadow it.
    A secret in a tool DESCRIPTION is masked."""
    from fastapi.responses import JSONResponse
    req = _make_request(_auth())
    tools_payload = {
        "jsonrpc": "2.0", "id": 53,
        "result": {"tools": [
            {"name": "echo", "description": f"echoes input; internal key {_RAW_SECRET_STANDALONE}"},
        ]},
    }
    raw = JSONResponse(content=tools_payload, status_code=200)
    req.json = AsyncMock(return_value=_list_body(53))
    with (
        patch.object(mcp_proxy, "_validate_org_scope", return_value=None),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "stdio", "command": "x"})),
        patch.object(mcp_proxy, "_get_enabled_tools",
                     AsyncMock(return_value={"default_scan_action": "redact"})),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "_adapter_forward", AsyncMock(return_value=raw)),
    ):
        resp = await mcp_proxy.org_mcp_jsonrpc("demo", "srv", req)
    blob = json.dumps(_decode(resp))
    assert _RAW_SECRET_STANDALONE not in blob, "secret in tool description egressed RAW"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
