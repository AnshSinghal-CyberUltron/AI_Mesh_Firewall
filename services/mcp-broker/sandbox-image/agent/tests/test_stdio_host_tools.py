"""Agent stdio host-tool install path (MCP_HOST_TOOLS)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SANDBOX_IMAGE = Path(__file__).resolve().parents[2]
SHARED_ROOT = Path(__file__).resolve().parents[4] / "shared"
for p in (str(SANDBOX_IMAGE), str(SHARED_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from ai_mesh_shared.mcp_host_tools import (
    HOST_TOOL_INSTALL_FAILED_PREFIX,
    HostToolValidationError,
    parse_host_tools_spec,
)

from agent import stdio_manager as m


@pytest.fixture(autouse=True)
def _reset_host_tool_cache():
    m._host_tools_installed.clear()
    yield
    m._host_tools_installed.clear()


def test_parse_integration_via_ensure_host_tools_spec():
    assert parse_host_tools_spec("pip:semgrep npm:eslint") == [
        ("pip", "semgrep"),
        ("npm", "eslint"),
    ]


def test_pip_install_calls_uv_tool_install():
    calls: list[list[str]] = []

    async def _run():
        async def fake_exec(*cmd, **kwargs):
            calls.append(list(cmd))
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        with patch.object(m.asyncio, "create_subprocess_exec", side_effect=fake_exec):
            await m._install_host_tool("pip", "semgrep")

    asyncio.run(_run())
    assert calls == [["uv", "tool", "install", "--quiet", "semgrep"]]
    assert "pip:semgrep" in m._host_tools_installed


def test_npm_install_calls_npm_global():
    calls: list[list[str]] = []

    async def _run():
        async def fake_exec(*cmd, **kwargs):
            calls.append(list(cmd))
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        with patch.object(m.asyncio, "create_subprocess_exec", side_effect=fake_exec):
            await m._install_host_tool("npm", "eslint")

    asyncio.run(_run())
    assert calls[0][:4] == ["npm", "install", "-g", "--no-audit"]


def test_install_failure_raises_stable_prefix():
    async def _run():
        async def fake_exec(*cmd, **kwargs):
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b"package not found"))
            proc.returncode = 1
            proc.kill = MagicMock()
            return proc

        with patch.object(m.asyncio, "create_subprocess_exec", side_effect=fake_exec):
            with pytest.raises(RuntimeError, match=HOST_TOOL_INSTALL_FAILED_PREFIX):
                await m._install_host_tool("pip", "totally-fake-pkg-xyz")

    asyncio.run(_run())


def test_install_timeout_fail_closed():
    async def _run():
        async def fake_exec(*cmd, **kwargs):
            proc = MagicMock()

            async def slow():
                await asyncio.sleep(10)
                return b"", b""

            proc.communicate = slow
            proc.kill = MagicMock()
            return proc

        with patch.object(m.asyncio, "create_subprocess_exec", side_effect=fake_exec):
            import ai_mesh_shared.mcp_host_tools_runtime as rt

            with patch.object(rt, "_HOST_TOOL_INSTALL_TIMEOUT", 0.01):
                with pytest.raises(RuntimeError, match="timed out"):
                    await m._install_host_tool("pip", "semgrep")

    asyncio.run(_run())


def test_second_spawn_skips_reinstall():
    call_count = 0

    async def _run():
        nonlocal call_count

        async def fake_exec(*cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        with patch.object(m.asyncio, "create_subprocess_exec", side_effect=fake_exec):
            await m._install_host_tool("pip", "semgrep")
            await m._install_host_tool("pip", "semgrep")

    asyncio.run(_run())
    assert call_count == 1


def test_allowlist_blocks_before_install(monkeypatch):
    monkeypatch.setenv("MCP_HOST_TOOLS_ALLOWLIST", "semgrep")

    async def _run():
        with pytest.raises(HostToolValidationError, match="allowlist"):
            await m._ensure_host_tools({"MCP_HOST_TOOLS": "pip:cowsay"})

    asyncio.run(_run())


def test_ensure_host_tools_called_before_spawn(monkeypatch):
    ensure = AsyncMock()
    monkeypatch.setattr(m, "_ensure_host_tools", ensure)
    monkeypatch.setattr(
        m,
        "_resolve_npx_spawn",
        AsyncMock(side_effect=lambda c, a: (c, a)),
    )
    monkeypatch.setattr(m, "_build_child_env", lambda *a, **k: {"PATH": "/usr/bin"})
    monkeypatch.setattr(m, "_processes", {})
    monkeypatch.setattr(m, "_MAX_PROCESSES_PER_ORG", 99)

    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.pid = 123
    proc.returncode = None

    async def _run():
        async def fake_spawn(*args, **kwargs):
            return proc

        with patch.object(m.asyncio, "create_subprocess_exec", side_effect=fake_spawn):
            with patch.object(m.asyncio, "create_task", return_value=MagicMock()):
                try:
                    await m._ensure_process(
                        "test-org/srv",
                        "npx",
                        ["-y", "pkg"],
                        {"MCP_HOST_TOOLS": "pip:semgrep"},
                    )
                finally:
                    m._processes.clear()

    asyncio.run(_run())
    ensure.assert_awaited_once()
    assert ensure.await_args.args[0]["MCP_HOST_TOOLS"] == "pip:semgrep"


def test_reject_invalid_package_in_spec():
    async def _run():
        with pytest.raises(HostToolValidationError, match="package"):
            await m._ensure_host_tools({"MCP_HOST_TOOLS": "pip:evil;rm"})

    asyncio.run(_run())


def test_should_resolve_npx_to_node_simple_pkg():
    assert m._should_resolve_npx_to_node("npx", ["-y", "mcp-server-semgrep"]) == "mcp-server-semgrep"
    assert m._should_resolve_npx_to_node("npx", ["-y", "mcp-remote", "https://x/mcp"]) is None


def test_package_dir_name_strips_version():
    assert m._package_dir_name("mcp-server-semgrep@1.0.1") == "mcp-server-semgrep"
    assert m._package_dir_name("@scope/pkg@2.0") == "@scope/pkg"


def test_resolve_npx_spawn_rewrites_to_node(tmp_path, monkeypatch):
    pkg_root = tmp_path / "node_modules" / "demo-mcp"
    pkg_root.mkdir(parents=True)
    entry = pkg_root / "build" / "index.js"
    entry.parent.mkdir()
    entry.write_text("console.log('ok')", encoding="utf-8")
    (pkg_root / "package.json").write_text(
        json.dumps({"name": "demo-mcp", "bin": {"demo-mcp": "build/index.js"}}),
        encoding="utf-8",
    )
    cache = tmp_path / "_npx" / "abc" / "node_modules" / "demo-mcp"
    cache.mkdir(parents=True)
    import shutil

    shutil.copytree(pkg_root, cache, dirs_exist_ok=True)

    monkeypatch.setattr(m, "_NPX_CACHE_ROOT", str(tmp_path / "_npx"))

    async def _run():
        cmd, args = await m._resolve_npx_spawn("npx", ["-y", "demo-mcp"])
        assert cmd == "node"
        assert args == [str(cache / "build" / "index.js")]

    asyncio.run(_run())


def test_classify_exit_reason_protocol_when_host_tools_ready():
    from ai_mesh_shared.mcp_host_tools import (
        HOST_CLI_TOOLS_INSTALLED_MARKER,
        MCP_PROTOCOL_HANDSHAKE_FAILED_MARKER,
    )

    reason = m._classify_exit_reason(0, "", oversized_line=False, host_tools_ready=True)
    assert MCP_PROTOCOL_HANDSHAKE_FAILED_MARKER in reason
    assert HOST_CLI_TOOLS_INSTALLED_MARKER in reason
    assert "missing host dependency" not in reason


def test_classify_exit_reason_missing_dep_when_host_tools_not_ready():
    reason = m._classify_exit_reason(0, "", oversized_line=False, host_tools_ready=False)
    assert "missing host dependency" in reason

