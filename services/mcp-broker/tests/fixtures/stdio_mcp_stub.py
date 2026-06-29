#!/usr/bin/env python3
"""Hermetic stdio MCP stub for sandbox-agent unit tests."""

from __future__ import annotations

import json
import os
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
        "description": "Return the value of an environment variable",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
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
                    "serverInfo": {"name": "stdio-mcp-stub", "version": "1.0.0"},
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
            else:
                text = f"unknown tool: {name}"
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
