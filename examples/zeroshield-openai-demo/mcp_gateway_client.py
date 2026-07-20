#!/usr/bin/env python3
"""ZeroShield MCP Client sample — Pattern C (gateway JSON-RPC).

Talks to a registered org MCP server through the gateway Streamable HTTP
endpoint. Auth is the org gateway API key (same key as OpenAI base_url=/v1).

    export ZEROSHIELD_API_KEY=zs_...
    export ZEROSHIELD_ORG_SLUG=zeroshield
    export MCP_SERVER_SLUG=everything-1
    export GATEWAY_HOST=http://127.0.0.1:8300   # host only — no /v1
    python mcp_gateway_client.py

Optional: ``pip install mcp`` — if the Streamable HTTP client imports cleanly
we try it first; otherwise we fall back to a minimal httpx JSON-RPC handshake
(initialize → tools/list → tools/call), which matches what VS Code sends.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _gateway_host() -> str:
    host = _env("GATEWAY_HOST")
    if host:
        return host.rstrip("/")
    base = _env("ZEROSHIELD_BASE_URL", "http://127.0.0.1:8300/v1").rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _mcp_url(org: str, server: str) -> str:
    return f"{_gateway_host()}/gateway/{org}/mcp/{server}"


def _mask(text: str) -> str:
    """Light masking for console output (never print full secrets if present)."""
    out = text
    for needle in ("sk-", "ghp_", "AKIA", "ASIA"):
        if needle in out:
            out = out.replace(needle, needle[0] + "***")
    return out[:4000]


def rpc_httpx(
    url: str,
    key: str,
    method: str,
    params: dict | None = None,
    *,
    id_: int = 1,
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    with httpx.Client(timeout=90.0) as client:
        resp = client.post(url, headers=headers, json=body)
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Non-JSON MCP response HTTP {resp.status_code}: {(resp.text or '')[:300]}") from exc
    if resp.status_code >= 400 and "error" not in data:
        raise RuntimeError(f"HTTP {resp.status_code}: {json.dumps(data)[:400]}")
    return data if isinstance(data, dict) else {"raw": data}


def run_httpx(url: str, key: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    print(f"→ initialize {url}")
    init = rpc_httpx(
        url,
        key,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "zeroshield-mcp-gateway-client", "version": "1.0"},
        },
        id_=1,
    )
    if init.get("error"):
        raise RuntimeError(f"initialize failed: {init['error']}")

    print("→ tools/list")
    listed = rpc_httpx(url, key, "tools/list", {}, id_=2)
    tools = ((listed.get("result") or {}).get("tools") or [])
    names = [t.get("name") for t in tools if isinstance(t, dict)]
    print(f"   tools ({len(names)}): {', '.join(names[:12])}{'…' if len(names) > 12 else ''}")
    chosen = tool if tool in names else (names[0] if names else tool)

    print(f"→ tools/call name={chosen}")
    called = rpc_httpx(
        url,
        key,
        "tools/call",
        {"name": chosen, "arguments": arguments},
        id_=3,
    )
    return {"initialize": init, "tools_list": listed, "tools_call": called, "tool": chosen}


def try_official_mcp(url: str, key: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort official SDK path; return None to fall back to httpx."""
    try:
        from mcp import ClientSession  # type: ignore
        from mcp.client.streamable_http import streamablehttp_client  # type: ignore
    except Exception:
        return None

    import anyio

    async def _run() -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {key}"}
        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = [t.name for t in (listed.tools or [])]
                chosen = tool if tool in names else (names[0] if names else tool)
                result = await session.call_tool(chosen, arguments)
                return {
                    "backend": "mcp-python-sdk",
                    "tools": names,
                    "tool": chosen,
                    "result": result.model_dump() if hasattr(result, "model_dump") else str(result),
                }

    try:
        return anyio.run(_run)
    except Exception as exc:  # noqa: BLE001
        print(f"(official mcp SDK path failed, falling back to httpx: {exc})", file=sys.stderr)
        return None


def main() -> int:
    key = _env("ZEROSHIELD_API_KEY")
    org = _env("ZEROSHIELD_ORG_SLUG")
    server = _env("MCP_SERVER_SLUG")
    tool = _env("MCP_TOOL_NAME", "echo")
    message = _env("MCP_ECHO_MESSAGE", "zeroshield-mcp-client-ok")
    if not key or not org or not server:
        print(
            "Usage: set ZEROSHIELD_API_KEY, ZEROSHIELD_ORG_SLUG, MCP_SERVER_SLUG "
            "(optional GATEWAY_HOST, MCP_TOOL_NAME, MCP_ECHO_MESSAGE)",
            file=sys.stderr,
        )
        return 2

    url = _mcp_url(org, server)
    arguments: dict[str, Any] = {"message": message}
    # many echo tools accept "message"; some use other shapes — still fine for list/handshake proof
    print(f"ZeroShield MCP gateway client\n  url={url}\n  org={org} server={server}")

    official = try_official_mcp(url, key, tool, arguments)
    if official is not None:
        print(_mask(json.dumps(official, indent=2, default=str)))
        print("\nNote: args/result were scanned by the gateway (redact/block) before egress.")
        return 0

    out = run_httpx(url, key, tool, arguments)
    call = out.get("tools_call") or {}
    print(_mask(json.dumps(call, indent=2, default=str)))
    blob = json.dumps(call)
    print(f"\nhas_echo_message={message in blob} tool={out.get('tool')}")
    print("Note: args/result were scanned by the gateway (redact/block) before egress.")
    if call.get("error"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
