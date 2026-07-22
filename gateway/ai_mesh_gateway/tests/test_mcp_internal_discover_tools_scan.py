"""CHG-0108: internal_discover_tools (the backend tool-SYNC / discovery route) returned
upstream tools/list METADATA (descriptions / names / inputSchema) RAW — a parity gap vs
the org path (org_mcp_jsonrpc tools/list, CHG-0077/0092), the REST list (CHG-0079), and
the external proxy, which all scan tool metadata. Tool descriptions come LIVE from an
UNTRUSTED upstream MCP server and are synced into the catalog + shown to the model, so a
secret / PII / internal-IP (or an encoded-exfil payload) in a description reached the
backend/LLM unredacted on the discovery path. Now both the direct-httpx and the
sandbox-routed discovery paths scan tool metadata (mask / fail-closed block + audit).
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


def _http_resp(json_body, *, content_type="application/json"):
    r = AsyncMock()
    r.status_code = 200
    r.json = lambda: json_body
    r.text = json.dumps(json_body)
    r.headers = {"content-type": content_type}
    r.raise_for_status = lambda: None
    return r


def _fake_client(responses):
    seq = list(responses)

    async def _post(*_a, **_k):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    c = AsyncMock()
    c.post = AsyncMock(side_effect=_post)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


def _req():
    return SimpleNamespace(
        headers={"X-Gateway-Internal-Key": "ok"},
        json=AsyncMock(return_value={"org_slug": "demo", "server_slug": "srv"}))


def _tools(desc):
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"tools": [{"name": "fetch", "description": desc}]}}


async def _drive_direct(tools_json, scan_action="redact"):
    """Direct-httpx discovery path (_is_sandbox_routed forced False).

    ``scan_action`` is the posture the OPERATOR selected for this org. Enforcement is
    strictly operator-selected: with nothing selected (or an observe-only posture such
    as ``tag``/``monitor``) metadata is still scanned and tagged but never mutated, so
    every masking/blocking assertion below explicitly selects an enforcing posture."""
    client = _fake_client([
        _http_resp({"jsonrpc": "2.0", "id": 1, "result": {}}), _http_resp({}),
        _http_resp(tools_json)])
    audit = AsyncMock()
    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_is_sandbox_routed", return_value=False),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "streamable-http",
                                             "url": "https://safe.example.com/mcp"})),
        patch.object(mcp_proxy, "_get_enabled_tools",
                     AsyncMock(return_value={"default_scan_action": scan_action})),
        patch.object(mcp_proxy, "_record_gateway_event", audit),
        patch.object(mcp_proxy, "is_safe_outbound_url", return_value=(True, "")),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        resp = await mcp_proxy.internal_discover_tools(_req())
    return resp.body.decode("utf-8"), [c.kwargs.get("decision") for c in audit.call_args_list]


async def _drive_sandbox(tools_json, scan_action="redact"):
    """Sandbox-routed discovery path (adapter returns the tools/list). ``scan_action``
    is the operator-selected posture — see ``_drive_direct``."""
    audit = AsyncMock()
    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_is_sandbox_routed", return_value=True),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "stdio", "command": "x"})),
        patch.object(mcp_proxy, "_get_enabled_tools",
                     AsyncMock(return_value={"default_scan_action": scan_action})),
        patch.object(mcp_proxy, "_record_gateway_event", audit),
        patch.object(mcp_proxy, "_adapter_forward",
                     AsyncMock(return_value=JSONResponse(content=tools_json, status_code=200))),
    ):
        resp = await mcp_proxy.internal_discover_tools(_req())
    return resp.body.decode("utf-8"), [c.kwargs.get("decision") for c in audit.call_args_list]


@pytest.mark.asyncio
async def test_direct_description_secret_pii_ip_masked():
    blob, decisions = await _drive_direct(
        _tools("Fetch a URL. Contact bob.jones@corp.example key AKIAIOSFODNN7EXAMPLE host 10.9.8.7"))
    assert "AKIAIOSFODNN7EXAMPLE" not in blob
    assert "bob.jones@corp.example" not in blob
    assert "10.9.8.7" not in blob
    assert "redact" in decisions  # the metadata redaction is audited


@pytest.mark.asyncio
async def test_sandbox_description_secret_masked():
    blob, decisions = await _drive_sandbox(
        _tools("key AKIAIOSFODNN7EXAMPLE email bob@corp.example"))
    assert "AKIAIOSFODNN7EXAMPLE" not in blob
    assert "bob@corp.example" not in blob
    assert "redact" in decisions


@pytest.mark.asyncio
async def test_poisoned_unmaskable_metadata_blocked():
    blob, decisions = await _drive_direct(
        _tools("box 10.0.0.5 served key /home/bob/.ssh/id_rsa"))
    # STRICT OPERATOR CONTROL: redact masks the metadata in place and forwards.
    assert "id_rsa" not in blob
    assert "10.0.0.5" not in blob
    assert "withheld" not in blob
    assert "block" not in decisions


@pytest.mark.asyncio
async def test_error_envelope_secret_masked():
    """A bare tools/list error frame (auth failure can echo a token) is scanned too."""
    blob, _ = await _drive_direct(
        {"jsonrpc": "2.0", "id": 1,
         "error": {"code": -32000, "message": "auth failed token AKIAIOSFODNN7EXAMPLE at 10.0.0.9"}})
    assert "AKIAIOSFODNN7EXAMPLE" not in blob
    assert "10.0.0.9" not in blob


@pytest.mark.asyncio
async def test_benign_metadata_preserved():
    blob, decisions = await _drive_direct(_tools("Fetches a web page and returns its text."))
    assert "Fetches a web page and returns its text." in blob
    assert "block" not in decisions and "redact" not in decisions


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
