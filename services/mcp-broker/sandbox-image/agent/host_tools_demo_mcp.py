"""Minimal stdio MCP server demonstrating MCP_HOST_TOOLS (cowsay on PATH).

Run after the sandbox agent installs declared host CLI tools:
    python3 -m agent.host_tools_demo_mcp
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

_TOOLS = [
    {
        "name": "moo",
        "description": "Say hello via cowsay (requires MCP_HOST_TOOLS pip:cowsay)",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    }
]


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _handle(msg: dict) -> None:
    method = msg.get("method")
    req_id = msg.get("id")
    if method == "initialize":
        _send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "host-tools-demo", "version": "1.0.0"},
                },
            }
        )
        return
    if method == "notifications/initialized":
        return
    if method == "tools/list":
        _send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": _TOOLS}})
        return
    if method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") != "moo":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "unknown tool"},
                }
            )
            return
        arguments = params.get("arguments") or {}
        message = str(arguments.get("message") or "moo")
        cowsay = shutil.which("cowsay")
        if not cowsay:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": "cowsay not on PATH"},
                }
            )
            return
        proc = subprocess.run(
            [cowsay, message],
            capture_output=True,
            text=True,
            check=False,
        )
        text = proc.stdout.strip() if proc.returncode == 0 else (proc.stderr or "cowsay failed")
        _send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": proc.returncode != 0,
                },
            }
        )
        return
    if method == "ping":
        _send({"jsonrpc": "2.0", "id": req_id, "result": {}})
        return
    if req_id is not None:
        _send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        )


def main() -> None:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict):
            _handle(msg)


if __name__ == "__main__":
    main()
