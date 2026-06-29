"""Branch tests: send_jsonrpc in-process vs broker delegation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_GW = Path(__file__).resolve().parents[1]
_SHARED = _GW.parent.parent / "shared"
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

from mcp_stdio_adapter import (
    _send_jsonrpc_in_process,
    send_jsonrpc,
)


ORG = "acme"
SERVER = "playwright"
COMMAND = "npx"
ARGS = ["-y", "@playwright/mcp@latest"]
ENV = {"LINEAR_API_KEY": "lin_test"}
SERVER_CONFIG = {
    "org_slug": ORG,
    "server_slug": SERVER,
    "command": COMMAND,
    "args": ARGS,
    "env_vars": ENV,
}


@pytest.fixture(autouse=True)
def _in_process_default(monkeypatch: pytest.MonkeyPatch):
    """Existing gateway tests assume in-process stdio unless overridden."""
    monkeypatch.setenv("MCP_STDIO_IN_PROCESS", "true")


@pytest.mark.asyncio
async def test_send_jsonrpc_broker_branch_delegates(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_STDIO_IN_PROCESS", "false")
    broker_result = {"jsonrpc": "2.0", "id": 7, "result": {"tools": []}}
    mock_broker = AsyncMock(return_value=broker_result)

    with patch(
        "ai_mesh_gateway.mcp_sandbox_client.broker_send_jsonrpc",
        mock_broker,
    ):
        result = await send_jsonrpc(
            org_slug=ORG,
            server_slug=SERVER,
            command=COMMAND,
            args=ARGS,
            env=ENV,
            method="tools/list",
            params=None,
            msg_id=7,
            server_config=SERVER_CONFIG,
        )

    assert result == broker_result
    mock_broker.assert_awaited_once_with(
        ORG,
        SERVER_CONFIG,
        "tools/list",
        None,
        msg_id=7,
    )


@pytest.mark.asyncio
async def test_send_jsonrpc_broker_branch_org_slug_from_server_config(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MCP_STDIO_IN_PROCESS", "false")
    cfg = dict(SERVER_CONFIG)
    cfg["org_slug"] = "tenant-b"
    mock_broker = AsyncMock(
        return_value={"jsonrpc": "2.0", "id": 1, "result": {}},
    )

    with patch(
        "ai_mesh_gateway.mcp_sandbox_client.broker_send_jsonrpc",
        mock_broker,
    ):
        await send_jsonrpc(
            org_slug=ORG,
            server_slug=SERVER,
            command=COMMAND,
            args=ARGS,
            env=ENV,
            method="initialize",
            params=None,
            msg_id=1,
            server_config=cfg,
        )

    assert mock_broker.await_args.args[0] == "tenant-b"


@pytest.mark.asyncio
async def test_send_jsonrpc_in_process_does_not_call_broker(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MCP_STDIO_IN_PROCESS", "true")
    mock_proc = MagicMock()
    mock_proc.initialized = True
    mock_proc.next_id.return_value = 99
    mock_proc.needs_reauth = False
    mock_proc.process = MagicMock(returncode=None)

    with patch(
        "mcp_stdio_adapter._ensure_process",
        AsyncMock(return_value=mock_proc),
    ), patch(
        "mcp_stdio_adapter._ensure_initialized",
        AsyncMock(),
    ), patch(
        "mcp_stdio_adapter._send_message",
        AsyncMock(return_value={"jsonrpc": "2.0", "id": 5, "result": {"ok": True}}),
    ), patch(
        "ai_mesh_gateway.mcp_sandbox_client.broker_send_jsonrpc",
        AsyncMock(),
    ) as mock_broker:
        result = await send_jsonrpc(
            org_slug=ORG,
            server_slug=SERVER,
            command=COMMAND,
            args=ARGS,
            env=ENV,
            method="tools/call",
            params={"name": "x", "arguments": {}},
            msg_id=5,
        )

    mock_broker.assert_not_awaited()
    assert result["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_send_jsonrpc_in_process_initialize_cached():
    mock_proc = MagicMock()
    mock_proc.initialized = True

    with patch(
        "mcp_stdio_adapter._ensure_process",
        AsyncMock(return_value=mock_proc),
    ), patch(
        "mcp_stdio_adapter._ensure_initialized",
        AsyncMock(),
    ):
        result = await _send_jsonrpc_in_process(
            org_slug=ORG,
            server_slug=SERVER,
            command=COMMAND,
            args=ARGS,
            env=ENV,
            method="initialize",
            params=None,
            msg_id=3,
        )

    assert result["id"] == 3
    assert result["result"]["protocolVersion"] == "2024-11-05"
