#!/usr/bin/env python3
"""Execute at least one tool on every connected MCP server via gateway JSON-RPC.

Writes mcp-parallel/findings/mcp-arch-validation-2026-07-08/fleet-tool-execution.json
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-arch-validation-2026-07-08/fleet-tool-execution.json"
MANIFEST = ROOT / "scripts/ralph/.mcp_scale_manifest.json"
CONTROL = "http://127.0.0.1:8100"
GATEWAY = "http://127.0.0.1:8300"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"
ORG = "zeroshield"
SSE_TIMEOUT_S = float(os.environ.get("FLEET_SSE_TIMEOUT_S", "180"))

# Prefer these tools when present (safe, idempotent).
PREFERRED = ("echo", "get-sum", "add", "fetch", "list_tools", "list_rules", "list_directory")


def login(c: httpx.Client) -> str:
    for attempt in range(8):
        r = c.post(f"{CONTROL}/api/auth/token/", json={"email": EMAIL, "password": PASS}, timeout=60)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "10"))
            time.sleep(min(wait, 45))
            continue
        r.raise_for_status()
        return r.json()["access"]
    r.raise_for_status()
    return ""


def pick_tool(tools: list[dict]) -> str | None:
    names = [
        t.get("tool_name") or t.get("name")
        for t in tools
        if t.get("tool_name") or t.get("name")
    ]
    for p in PREFERRED:
        if p in names:
            return p
    return names[0] if names else None


def tool_args(name: str) -> dict:
    if name == "echo":
        return {"message": f"fleet-{uuid.uuid4().hex[:8]}"}
    if name in ("add", "get-sum"):
        return {"a": 2, "b": 3}
    if name == "fetch":
        return {"url": "https://example.com"}
    return {}


def gw_call(
    c: httpx.Client,
    key: str,
    server_slug: str,
    tool: str,
    arguments: dict,
    *,
    timeout_s: float = 120,
    attempts: int = 1,
) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": f"fleet-{uuid.uuid4().hex[:8]}",
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    last: dict = {"ok": False, "error": "no_attempt"}
    for attempt in range(max(1, attempts)):
        t0 = time.perf_counter()
        try:
            r = c.post(
                f"{GATEWAY}/gateway/{ORG}/mcp/{server_slug}",
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
                timeout=timeout_s,
            )
            ms = round((time.perf_counter() - t0) * 1000, 1)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            err = body.get("error")
            result = body.get("result") or {}
            content = result.get("content") or []
            text = content[0].get("text", "") if content else ""
            blocked = "[BLOCKED]" in text or bool(err)
            last = {
                "status": r.status_code,
                "latency_ms": ms,
                "ok": r.status_code == 200 and not err and not blocked,
                "blocked": blocked,
                "error": err,
                "egress_preview": (text or str(err))[:200],
                "attempt": attempt + 1,
            }
            if last["ok"]:
                return last
        except Exception as exc:
            last = {
                "ok": False,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": str(exc),
                "attempt": attempt + 1,
            }
        if attempt + 1 < attempts:
            time.sleep(2)
    return last


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    mf = json.loads(MANIFEST.read_text(encoding="utf-8"))
    key = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)
    report: dict = {"org": ORG, "servers": [], "fleetPass": False}

    with httpx.Client() as c:
        tok = login(c)
        h = {"Authorization": f"Bearer {tok}"}
        servers = c.get(f"{CONTROL}/api/mcp-connector/servers/", headers=h, timeout=30).json()
        rows = servers if isinstance(servers, list) else servers.get("results", [])
        connected = [
            s for s in rows
            if s.get("connection_status") == "connected" and (s.get("tools_count") or 0) > 0
        ]
        report["connected_count"] = len(connected)

        for srv in connected:
            slug = srv["server_slug"]
            sid = srv["id"]
            transport = srv.get("transport")
            tr = c.get(f"{CONTROL}/api/mcp-connector/servers/{sid}/tools/", headers=h, timeout=60)
            tools = tr.json() if tr.is_success else []
            if isinstance(tools, dict):
                tools = tools.get("tools") or tools.get("results") or []
            tool = pick_tool(tools if isinstance(tools, list) else [])
            row = {
                "server_slug": slug,
                "transport": transport,
                "tools_count": srv.get("tools_count"),
                "tool": tool,
                "tools_list_ok": tr.is_success,
            }
            if not tool:
                row["ok"] = False
                row["reason"] = "no_tool"
            else:
                args = tool_args(tool)
                is_sse = transport == "sse"
                if is_sse:
                    gw_call(c, key, slug, tool, {"message": "fleet-warmup"}, timeout_s=SSE_TIMEOUT_S, attempts=1)
                row.update(
                    gw_call(
                        c,
                        key,
                        slug,
                        tool,
                        args,
                        timeout_s=SSE_TIMEOUT_S if is_sse else 120,
                        attempts=3 if is_sse else 1,
                    )
                )
            report["servers"].append(row)
            print(f"{slug} ({transport}) tool={tool} ok={row.get('ok')} {row.get('latency_ms')}ms")

    ok_n = sum(1 for s in report["servers"] if s.get("ok"))
    report["ok_count"] = ok_n
    report["fail_count"] = len(report["servers"]) - ok_n
    report["fleetPass"] = ok_n >= max(1, int(len(report["servers"]) * 0.85))
    report["strictFleetPass"] = ok_n == len(report["servers"])
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"fleetPass": report["fleetPass"], "strictFleetPass": report["strictFleetPass"], "ok": ok_n, "total": len(report["servers"])}, indent=2))
    return 0 if report["strictFleetPass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
