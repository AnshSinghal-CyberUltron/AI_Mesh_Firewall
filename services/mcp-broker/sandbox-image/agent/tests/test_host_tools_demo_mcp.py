"""Unit tests for the host-tools demo MCP stdio server."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SANDBOX_IMAGE = Path(__file__).resolve().parents[2]
if str(SANDBOX_IMAGE) not in sys.path:
    sys.path.insert(0, str(SANDBOX_IMAGE))

from agent import host_tools_demo_mcp as demo  # noqa: F401 — ensures module importable


def test_tools_list_via_stdio():
    init = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
    )
    listed = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    proc = subprocess.run(
        [sys.executable, "-m", "agent.host_tools_demo_mcp"],
        input=f"{init}\n{listed}\n",
        capture_output=True,
        text=True,
        cwd=str(SANDBOX_IMAGE),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) >= 2
    tools_msg = json.loads(lines[1])
    assert tools_msg["id"] == 2
    names = [t["name"] for t in tools_msg["result"]["tools"]]
    assert "moo" in names
