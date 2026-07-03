"""CHG-0120: the internal (chat-pipeline) routes internal_tools_call + internal_discover_tools
recorded MCPEvents with NO request_id and forwarded to the broker adapter with NO correlation_id
— so a chat-pipeline tool call / tool-sync trace ENDED at the internal gateway boundary (unlike
org_mcp_jsonrpc / the bare REST route, which propagate the X-Request-ID, CHG-0050/0051). Both
routes now thread _mcp_request_correlation_id(request) into every audit event AND the
_adapter_forward hop, so the trace is continuous gateway → broker → sandbox.
"""
from __future__ import annotations

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

_RID = "trace-corr-99"
_REDACT = {"default_scan_action": "redact"}


def _req(body):
    return SimpleNamespace(
        headers={"X-Gateway-Internal-Key": "ok", "x-request-id": _RID},
        json=AsyncMock(return_value=body))


async def _drive(fn_name, body, adapter_result):
    audit = AsyncMock()
    fwd_corr = []

    async def _fwd(transport, cfg, org, srv, call_body, jr, mid, *, correlation_id=""):
        fwd_corr.append(correlation_id)
        return JSONResponse(content=adapter_result, status_code=200)

    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_is_sandbox_routed", return_value=True),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "stdio", "command": "x"})),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=_REDACT)),
        patch.object(mcp_proxy, "_record_gateway_event", audit),
        patch.object(mcp_proxy, "_adapter_forward", AsyncMock(side_effect=_fwd)),
    ):
        await getattr(mcp_proxy, fn_name)(_req(body))
    return [c.kwargs.get("request_id") for c in audit.call_args_list], fwd_corr


@pytest.mark.asyncio
async def test_internal_tools_call_propagates_correlation_id():
    # redact-configured server + PII args + PII result → both inbound-redact and
    # result-redact audit events fire, and both must carry the X-Request-ID.
    result = {"jsonrpc": "2.0", "id": 1,
              "result": {"content": [{"type": "text", "text": "user bob@corp.example"}]}}
    audit_rids, fwd_corr = await _drive(
        "internal_tools_call",
        {"org_slug": "demo", "server_slug": "srv", "tool_name": "fetch",
         "arguments": {"note": "email bob@corp.example"}},
        result)
    assert audit_rids and all(r == _RID for r in audit_rids)   # every audit event correlated
    assert fwd_corr == [_RID]                                  # broker hop correlated


@pytest.mark.asyncio
async def test_internal_discover_tools_propagates_correlation_id():
    # poisoned tool description → the metadata-scan audit fires + must carry the X-Request-ID,
    # and the adapter forward must carry the correlation_id.
    result = {"jsonrpc": "2.0", "id": 1, "result": {
        "tools": [{"name": "f", "description": "key AKIAIOSFODNN7EXAMPLE"}]}}
    audit_rids, fwd_corr = await _drive(
        "internal_discover_tools",
        {"org_slug": "demo", "server_slug": "srv"}, result)
    assert audit_rids and all(r == _RID for r in audit_rids)
    assert fwd_corr == [_RID]


@pytest.mark.asyncio
async def test_missing_x_request_id_falls_back_not_crash():
    # No X-Request-ID header → correlation id falls back to the JSON-RPC id ("1"), never crashes.
    audit = AsyncMock()
    result = {"jsonrpc": "2.0", "id": 1,
              "result": {"content": [{"type": "text", "text": "user bob@corp.example"}]}}
    req = SimpleNamespace(headers={"X-Gateway-Internal-Key": "ok"},
                          json=AsyncMock(return_value={
                              "org_slug": "demo", "server_slug": "srv", "tool_name": "fetch",
                              "arguments": {"note": "email bob@corp.example"}}))
    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_is_sandbox_routed", return_value=True),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "stdio", "command": "x"})),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=_REDACT)),
        patch.object(mcp_proxy, "_record_gateway_event", audit),
        patch.object(mcp_proxy, "_adapter_forward",
                     AsyncMock(return_value=JSONResponse(content=result, status_code=200))),
    ):
        resp = await mcp_proxy.internal_tools_call(req)
    assert resp.status_code == 200
    rids = [c.kwargs.get("request_id") for c in audit.call_args_list]
    assert rids and all(r == "1" for r in rids)  # fell back to the JSON-RPC id, no crash


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
