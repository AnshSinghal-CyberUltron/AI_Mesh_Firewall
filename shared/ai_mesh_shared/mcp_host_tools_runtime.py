"""Async install of MCP_HOST_TOOLS declared CLI binaries (sandbox + gateway dev path)."""

from __future__ import annotations

import asyncio
import logging
import os

from ai_mesh_shared.mcp_host_tools import (
    HOST_TOOL_INSTALL_FAILED_PREFIX,
    assert_host_tool_allowed,
    host_tool_install_cmd,
    parse_host_tools_spec,
)

LOG = logging.getLogger("ai_mesh_shared.mcp_host_tools_runtime")

_HOST_TOOL_INSTALL_TIMEOUT = float(os.environ.get("MCP_HOST_TOOL_INSTALL_TIMEOUT", "300"))
_installed: set[str] = set()
_lock = asyncio.Lock()


def reset_host_tool_install_cache() -> None:
    """Test helper — clear the per-process install marker cache."""
    _installed.clear()


async def install_host_tool(manager: str, package: str) -> None:
    """Install one declared host CLI tool (idempotent per process)."""
    marker = f"{manager}:{package}"
    if marker in _installed:
        return
    async with _lock:
        if marker in _installed:
            return
        assert_host_tool_allowed(package)
        cmd = host_tool_install_cmd(manager, package)
        LOG.info("Installing declared host tool %s via %s", package, manager)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"{HOST_TOOL_INSTALL_FAILED_PREFIX} installer '{cmd[0]}' unavailable"
            ) from exc
        try:
            _out, _err = await asyncio.wait_for(
                proc.communicate(), timeout=_HOST_TOOL_INSTALL_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise RuntimeError(
                f"{HOST_TOOL_INSTALL_FAILED_PREFIX} '{package}' timed out after "
                f"{int(_HOST_TOOL_INSTALL_TIMEOUT)}s"
            ) from exc
        if proc.returncode != 0:
            tail = (_err or b"").decode("utf-8", "replace").strip()[-300:]
            raise RuntimeError(
                f"{HOST_TOOL_INSTALL_FAILED_PREFIX} '{package}' ({manager}): {tail}"
            )
        _installed.add(marker)
        LOG.info("Host tool %s installed", package)


async def ensure_host_tools(requested_env: dict[str, str]) -> None:
    """Install every tool declared in ``MCP_HOST_TOOLS`` before spawning the server."""
    for manager, package in parse_host_tools_spec(requested_env.get("MCP_HOST_TOOLS") or ""):
        await install_host_tool(manager, package)
