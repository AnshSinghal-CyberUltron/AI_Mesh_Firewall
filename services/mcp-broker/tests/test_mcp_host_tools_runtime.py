"""Async runtime install tests for MCP_HOST_TOOLS."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_mesh_shared.mcp_host_tools_runtime import (
    ensure_host_tools,
    install_host_tool,
    reset_host_tool_install_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_host_tool_install_cache()
    yield
    reset_host_tool_install_cache()


def test_ensure_host_tools_parses_spec():
    calls: list[tuple[str, str]] = []

    async def _run():
        async def fake_install(manager, package):
            calls.append((manager, package))

        with patch(
            "ai_mesh_shared.mcp_host_tools_runtime.install_host_tool",
            side_effect=fake_install,
        ):
            await ensure_host_tools({"MCP_HOST_TOOLS": "pip:semgrep npm:eslint"})

    asyncio.run(_run())
    assert calls == [("pip", "semgrep"), ("npm", "eslint")]


def test_install_host_tool_uv_command():
    calls: list[list[str]] = []

    async def _run():
        async def fake_exec(*cmd, **kwargs):
            calls.append(list(cmd))
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await install_host_tool("pip", "semgrep")

    asyncio.run(_run())
    assert calls == [["uv", "tool", "install", "--quiet", "semgrep"]]
