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


async def _run_adapter_error(request, body, *, enabled_info, error_obj):
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
async def test_error_envelope_secret_and_ip_redacted_under_tag_default():
    """The bug: a secret + internal IP inside error.message must be MASKED on egress,
    not returned raw, on the stdio adapter path under the default "tag" posture."""
    req = _make_request(_auth())
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled", return_value=True):
        resp = await _run_adapter_error(
            req, _call_body(31), enabled_info=None,
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
async def test_error_envelope_flag_disabled_leaves_raw():
    """With GATEWAY_MCP_REDACT_RESULT_ON_DETECT OFF the raw secret survives — proves
    the floor (not some unrelated path) is what masks the error envelope."""
    req = _make_request(_auth())
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled", return_value=False):
        resp = await _run_adapter_error(
            req, _call_body(33), enabled_info=None,
            error_obj={"code": -32000, "message": _LEAKY_ERR_MSG},
        )
    blob = json.dumps(_decode(resp))
    assert _RAW_SECRET in blob  # flag OFF → not force-redacted → raw present


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
            req, _call_body(35), enabled_info=None,
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
