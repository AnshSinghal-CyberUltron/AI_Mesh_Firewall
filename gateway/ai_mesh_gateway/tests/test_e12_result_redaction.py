"""E12 — MCP tool-RESULT redaction floor (symmetric to FIX1 arg credential block).

PROBLEM (fail-open): under the DEFAULT scan_action "tag" (and "monitor"), the
OUTBOUND tool-RESULT scan DETECTS a secret/PII but does NOT redact/block it, so a
tool result carrying token=ghp_... or an SSN reaches the LLM/client RAW.

FIX: a result-REDACTION floor. When GATEWAY_MCP_REDACT_RESULT_ON_DETECT is ON,
the resolved output action is not already redact/block, the explicit per-tool
action is not "monitor", and the output scan DETECTED a secret OR PII, the result
is force-REDACTED (masked, never blocked) before it is returned — on BOTH the
streamable-http and adapter (stdio/ws) transports.

Run:
    cd .../gateway && .venv/bin/python -m pytest \
        ai_mesh_gateway/tests/test_e12_result_redaction.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# Bootstrap import paths (same as test_e12_mcp_security.py).
_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402
from middleware import AuthContext  # noqa: E402


@pytest.fixture(autouse=True)
def _force_direct_http_path(monkeypatch):
    # These tests mock the direct-httpx streamable-http upstream to exercise the
    # transport-agnostic scan/redaction pipeline. Pin the legacy direct path
    # (MCP_HTTP_VIA_SANDBOX off) so the mock is hit; the sandbox-routed path is
    # covered by the stdio adapter tests + test_mcp_http_via_sandbox.py.
    monkeypatch.setenv("MCP_HTTP_VIA_SANDBOX", "0")


# A tool RESULT carrying BOTH a real-looking secret AND an SSN.
_RAW_TOKEN = "ghp_REALLOOKINGSECRET1234"
_RAW_SSN = "123-45-6789"
_SECRET_PII_RESULT_TEXT = (
    f"here is the api token={_RAW_TOKEN} and the customer ssn {_RAW_SSN} ok"
)
_BENIGN_RESULT_TEXT = "the weather in Paris today is sunny and mild"
_BENIGN_ARG = {"q": "what is the weather in Paris today"}


# ── shared fakes ────────────────────────────────────────────────────────────


def _make_request(auth):
    return SimpleNamespace(state=SimpleNamespace(auth_context=auth))


def _auth(org_slug="demo"):
    payload = {
        "key_id": "k1",
        "user_id": 1,
        "project_id": "p1",
        "org_slug": org_slug,
        "mcp_allowed_tools": [],
        "mcp_max_tool_calls": 0,
    }
    return AuthContext(key_hash="h" * 64, payload=payload)


def _decode(resp):
    import json
    return json.loads(resp.body.decode("utf-8"))


async def _run_streamable(request, body, *, enabled_info, backend_result):
    """Drive org_mcp_jsonrpc on the streamable-http path; backend returns
    ``backend_result`` as its ``result`` field."""
    backend_resp = AsyncMock()
    backend_resp.status_code = 200
    backend_resp.json = lambda: {"result": backend_result}
    fake_client = AsyncMock()
    fake_client.post = AsyncMock(return_value=backend_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    request.json = AsyncMock(return_value=body)
    with (
        patch.object(mcp_proxy, "_validate_org_scope", return_value=None),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "streamable-http"})),
        patch.object(mcp_proxy, "_get_enabled_tools",
                     AsyncMock(return_value=enabled_info)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "_incr_tool_call_count", AsyncMock(return_value=1)),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=fake_client),
    ):
        return await mcp_proxy.org_mcp_jsonrpc("demo", "srv", request)


async def _run_adapter(request, body, *, enabled_info, adapter_result):
    """Drive org_mcp_jsonrpc on the stdio adapter path; adapter returns a
    JSON-RPC response whose ``result`` is ``adapter_result``."""
    from fastapi.responses import JSONResponse
    raw = JSONResponse(content={
        "jsonrpc": "2.0", "id": body["id"], "result": adapter_result,
    }, status_code=200)
    request.json = AsyncMock(return_value=body)
    with (
        patch.object(mcp_proxy, "_validate_org_scope", return_value=None),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "stdio", "command": "x"})),
        patch.object(mcp_proxy, "_get_enabled_tools",
                     AsyncMock(return_value=enabled_info)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "_incr_tool_call_count", AsyncMock(return_value=1)),
        patch.object(mcp_proxy, "_adapter_forward", AsyncMock(return_value=raw)),
    ):
        return await mcp_proxy.org_mcp_jsonrpc("demo", "srv", request)


# ── helper unit coverage ─────────────────────────────────────────────────────


def test_findings_have_secret_or_pii_helper():
    """Floor predicate fires on secret OR PII findings (not on clean/empty)."""
    assert mcp_proxy._findings_have_secret_or_pii([{"threat_type": "secret"}]) is True
    assert mcp_proxy._findings_have_secret_or_pii([{"threat_type": "pii"}]) is True
    # combined pii+secret finding the orchestrator labels "pii"
    assert mcp_proxy._findings_have_secret_or_pii(
        [{"threat_type": "pii", "entity_type": "github_token",
          "detail": "Matched: github_token, token_assignment"}]) is True
    # a non-PII / non-secret finding (e.g. policy) → not a floor trigger by itself
    assert mcp_proxy._findings_have_secret_or_pii(
        [{"threat_type": "policy", "entity_type": "x"}]) is False
    assert mcp_proxy._findings_have_secret_or_pii([]) is False
    assert mcp_proxy._findings_have_secret_or_pii(None) is False


def test_redact_result_on_detect_default_on():
    """Flag defaults ON via env (no live CONFIG in this harness)."""
    import os
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GATEWAY_MCP_REDACT_RESULT_ON_DETECT", None)
        assert mcp_proxy._mcp_redact_result_on_detect_enabled() is True


# ── streamable-http transport ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streamable_secret_and_pii_result_redacted_under_tag_default():
    """A tool RESULT carrying a secret AND an SSN is REDACTED under the default
    "tag" scan_action — the raw token and raw SSN are absent from the return."""
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "echo", "arguments": _BENIGN_ARG}}
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled",
                      return_value=True):
        resp = await _run_streamable(
            req, body,
            enabled_info=None,  # → default scan_action "tag"
            backend_result=_SECRET_PII_RESULT_TEXT,
        )
    blob = str(_decode(resp))
    assert _RAW_TOKEN not in blob
    assert _RAW_SSN not in blob


@pytest.mark.asyncio
async def test_streamable_benign_result_passes_unchanged():
    """A benign tool RESULT (no secret/PII) is returned unchanged — the floor
    must not mutate clean content."""
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "echo", "arguments": _BENIGN_ARG}}
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled",
                      return_value=True):
        resp = await _run_streamable(
            req, body,
            enabled_info=None,
            backend_result=_BENIGN_RESULT_TEXT,
        )
    blob = str(_decode(resp))
    assert _BENIGN_RESULT_TEXT in blob
    assert "***" not in blob
    assert _decode(resp)["result"].get("isError") is not True


@pytest.mark.asyncio
async def test_streamable_flag_disabled_does_not_force_redact():
    """With GATEWAY_MCP_REDACT_RESULT_ON_DETECT OFF, the raw secret/SSN survive
    under the default "tag" action (proves the flag actually gates the floor)."""
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": 13, "method": "tools/call",
            "params": {"name": "echo", "arguments": _BENIGN_ARG}}
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled",
                      return_value=False):
        resp = await _run_streamable(
            req, body,
            enabled_info=None,
            backend_result=_SECRET_PII_RESULT_TEXT,
        )
    blob = str(_decode(resp))
    # flag OFF → not force-redacted → raw values still present
    assert _RAW_TOKEN in blob
    assert _RAW_SSN in blob


@pytest.mark.asyncio
async def test_streamable_explicit_monitor_does_not_force_redact():
    """An explicit per-tool "monitor" action is an operator observe-only override
    and still wins — the floor does NOT redact (same carve-out as FIX1)."""
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": 14, "method": "tools/call",
            "params": {"name": "echo", "arguments": _BENIGN_ARG}}
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled",
                      return_value=True):
        resp = await _run_streamable(
            req, body,
            enabled_info={"tool_scan_actions": {"echo": "monitor"}},
            backend_result=_SECRET_PII_RESULT_TEXT,
        )
    blob = str(_decode(resp))
    # monitor wins → observe-only → raw values pass through
    assert _RAW_TOKEN in blob
    assert _RAW_SSN in blob


# ── adapter (stdio/ws) transport parity ──────────────────────────────────────


@pytest.mark.asyncio
async def test_adapter_secret_and_pii_result_redacted_under_tag_default():
    """Parity: the stdio adapter path also REDACTS a secret+SSN tool RESULT under
    the default "tag" action."""
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": 15, "method": "tools/call",
            "params": {"name": "echo", "arguments": _BENIGN_ARG}}
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled",
                      return_value=True):
        resp = await _run_adapter(
            req, body,
            enabled_info=None,  # → default scan_action "tag"
            adapter_result={"content": [{"type": "text",
                                         "text": _SECRET_PII_RESULT_TEXT}]},
        )
    blob = str(_decode(resp))
    assert _RAW_TOKEN not in blob
    assert _RAW_SSN not in blob


@pytest.mark.asyncio
async def test_adapter_explicit_monitor_does_not_force_redact():
    """Parity: an explicit per-tool "monitor" still wins on the adapter path."""
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": 16, "method": "tools/call",
            "params": {"name": "echo", "arguments": _BENIGN_ARG}}
    with patch.object(mcp_proxy, "_mcp_redact_result_on_detect_enabled",
                      return_value=True):
        resp = await _run_adapter(
            req, body,
            enabled_info={"tool_scan_actions": {"echo": "monitor"}},
            adapter_result={"content": [{"type": "text",
                                         "text": _SECRET_PII_RESULT_TEXT}]},
        )
    blob = str(_decode(resp))
    assert _RAW_TOKEN in blob
    assert _RAW_SSN in blob
