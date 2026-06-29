#!/usr/bin/env python3
"""Adversarial stdio MCP stub — probes escape/leak via tools/call."""

from __future__ import annotations

import json
import os
import subprocess
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo a message",
        "inputSchema": {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
        },
    },
    {
        "name": "get_env",
        "description": "Return env var value",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "probe_read",
        "description": "Try to read a host path (adversarial)",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "probe_exec",
        "description": "Run shell probe (adversarial)",
        "inputSchema": {
            "type": "object",
            "properties": {"cmd": {"type": "string"}},
            "required": ["cmd"],
        },
    },
]


def _respond(req_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        req_id = msg.get("id")

        if method == "initialize":
            _respond(
                req_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "adversarial-mcp-stub", "version": "1.0.0"},
                },
            )
            continue

        if method == "notifications/initialized":
            continue

        if method == "tools/list":
            _respond(req_id, {"tools": TOOLS})
            continue

        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo":
                text = str(arguments.get("msg", ""))
            elif name == "get_env":
                text = os.environ.get(str(arguments.get("key", "")), "")
            elif name == "probe_read":
                path = str(arguments.get("path", ""))
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        text = fh.read(512)
                except OSError as exc:
                    text = f"BLOCKED:{exc}"
            elif name == "probe_exec":
                cmd = str(arguments.get("cmd", ""))
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                text = (proc.stdout or "") + (proc.stderr or "")
            else:
                text = f"unknown:{name}"
            _respond(
                req_id,
                {"content": [{"type": "text", "text": text}], "isError": False},
            )
            continue

        sys.stdout.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"unknown method: {method}"},
                }
            )
            + "\n"
        )
        sys.stdout.flush()


if __name__ == "__main__":
    main()
