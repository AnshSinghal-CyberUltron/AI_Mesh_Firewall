"""Internal tools-call must honor an actor forwarded by the control plane.

Previously ``internal_tools_call`` always passed ``actor=None`` into the scan
wrappers, so actor-scoped MCP policies / field RBAC could not fire on the
chat-pipeline path (even when control already had the actor). Control now
forwards ``body["actor"] = {user_id, agent_id, roles}``; the gateway must
thread it into ``_scan_tool_args_block`` / ``_scan_tool_result_floor``.
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


def _req(body):
    return SimpleNamespace(
        headers={"X-Gateway-Internal-Key": "ok", "x-request-id": "actor-test-1"},
        json=AsyncMock(return_value=body),
    )


@pytest.mark.asyncio
async def test_internal_tools_call_threads_actor_into_arg_scan(monkeypatch):
    actor_seen = {}

    async def _fake_args_block(arguments, **kwargs):
        actor_seen["actor"] = kwargs.get("actor")
        return arguments, False, [], [], {}

    async def _fake_floor(payload, **kwargs):
        actor_seen["floor_actor"] = kwargs.get("actor")
        return payload, False, [], [], {}

    monkeypatch.setattr(mcp_proxy, "_valid_internal_key", lambda k: True)
    monkeypatch.setattr(mcp_proxy, "_mcp_body_too_large", lambda r: False)
    monkeypatch.setattr(mcp_proxy, "_mcp_read_body_capped", AsyncMock())
    monkeypatch.setattr(
        mcp_proxy,
        "_get_server_config",
        AsyncMock(return_value={"transport": "stdio", "url": ""}),
    )
    monkeypatch.setattr(mcp_proxy, "_apply_fresh_config_overrides", lambda c, *a, **k: c)
    monkeypatch.setattr(
        mcp_proxy,
        "_get_enabled_tools",
        AsyncMock(return_value={"scan_controls_configured": False, "disabled": []}),
    )
    monkeypatch.setattr(mcp_proxy, "_is_tool_disabled", lambda *a, **k: False)
    monkeypatch.setattr(mcp_proxy, "_is_sandbox_routed", lambda t: True)
    monkeypatch.setattr(mcp_proxy, "_scan_tool_args_block", _fake_args_block)
    monkeypatch.setattr(mcp_proxy, "_scan_tool_result_floor", _fake_floor)
    monkeypatch.setattr(mcp_proxy, "_record_gateway_event", AsyncMock())
    monkeypatch.setattr(
        mcp_proxy,
        "_adapter_forward",
        AsyncMock(
            return_value=JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "ok"}]},
                }
            )
        ),
    )

    body = {
        "org_slug": "zeroshield",
        "server_slug": "everything-1",
        "tool_name": "echo",
        "arguments": {"message": "hi"},
        "actor": {"user_id": 42, "agent_id": "abc12345", "roles": ["analyst"]},
    }
    resp = await mcp_proxy.internal_tools_call(_req(body))
    assert resp.status_code == 200
    assert actor_seen["actor"] == {
        "user_id": 42,
        "agent_id": "abc12345",
        "roles": ["analyst"],
    }
    assert actor_seen["floor_actor"] == actor_seen["actor"]


@pytest.mark.asyncio
async def test_internal_tools_call_actor_none_when_absent(monkeypatch):
    actor_seen = {}

    async def _fake_args_block(arguments, **kwargs):
        actor_seen["actor"] = kwargs.get("actor")
        return arguments, False, [], [], {}

    async def _fake_floor(payload, **kwargs):
        return payload, False, [], [], {}

    monkeypatch.setattr(mcp_proxy, "_valid_internal_key", lambda k: True)
    monkeypatch.setattr(mcp_proxy, "_mcp_body_too_large", lambda r: False)
    monkeypatch.setattr(mcp_proxy, "_mcp_read_body_capped", AsyncMock())
    monkeypatch.setattr(
        mcp_proxy,
        "_get_server_config",
        AsyncMock(return_value={"transport": "stdio", "url": ""}),
    )
    monkeypatch.setattr(mcp_proxy, "_apply_fresh_config_overrides", lambda c, *a, **k: c)
    monkeypatch.setattr(
        mcp_proxy,
        "_get_enabled_tools",
        AsyncMock(return_value={"scan_controls_configured": False, "disabled": []}),
    )
    monkeypatch.setattr(mcp_proxy, "_is_tool_disabled", lambda *a, **k: False)
    monkeypatch.setattr(mcp_proxy, "_is_sandbox_routed", lambda t: True)
    monkeypatch.setattr(mcp_proxy, "_scan_tool_args_block", _fake_args_block)
    monkeypatch.setattr(mcp_proxy, "_scan_tool_result_floor", _fake_floor)
    monkeypatch.setattr(mcp_proxy, "_record_gateway_event", AsyncMock())
    monkeypatch.setattr(
        mcp_proxy,
        "_adapter_forward",
        AsyncMock(
            return_value=JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "ok"}]},
                }
            )
        ),
    )

    body = {
        "org_slug": "zeroshield",
        "server_slug": "everything-1",
        "tool_name": "echo",
        "arguments": {"message": "hi"},
    }
    resp = await mcp_proxy.internal_tools_call(_req(body))
    assert resp.status_code == 200
    assert actor_seen["actor"] is None
