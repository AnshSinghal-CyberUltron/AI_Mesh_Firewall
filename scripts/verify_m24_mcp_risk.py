#!/usr/bin/env python3
"""M2.4 MCP & Context Risk — Steps 1–3 API verification."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8100").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
PERIOD = "24h"
RISK_URL = f"{BASE}/api/module2/mcp/risk/?period={PERIOD}"

# Dry-run: triggers seeded MCP PII email redact rule (policy_domain=mcp).
DRY_RUN_ARGS = {"message": "contact admin@zeroshield.io for credentials"}
# Live: missing required field -> schema_validation_failed block (records telemetry).
LIVE_BLOCK_ARGS = {}


def request(method: str, url: str, *, headers: dict | None = None, body: dict | None = None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def summary_snapshot(payload: dict) -> dict:
    s = payload.get("summary") or {}
    return {
        "total_events": s.get("total_events", 0),
        "blocked_tool_calls": s.get("blocked_tool_calls", 0),
        "redacted_arguments": s.get("redacted_arguments", 0),
        "unique_tools": s.get("unique_tools", 0),
    }


def ensure_mcp_server(auth: dict) -> dict:
    code, servers = request("GET", f"{BASE}/api/mcp-connector/servers/", headers=auth)
    items = servers if isinstance(servers, list) else (servers.get("results") or [])
    existing = next((s for s in items if s.get("server_slug") == "everything-mcp"), None)
    if existing:
        return existing
    body = {
        "name": "everything-mcp",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"],
        "description": "M24 E2E stdio MCP server",
    }
    code, created = request("POST", f"{BASE}/api/mcp-connector/servers/", headers=auth, body=body)
    if code not in (200, 201):
        existing_server = created.get("existing_server") if isinstance(created, dict) else None
        if existing_server:
            return existing_server
        detail = created.get("url") or created.get("error") or created
        raise RuntimeError(f"Failed to register everything-mcp (HTTP {code}): {detail}")
    server = created
    sid = server.get("id")
    if sid:
        for _ in range(6):
            request("POST", f"{BASE}/api/mcp-connector/servers/{sid}/tools/", headers=auth, body={})
            code, tools = request("GET", f"{BASE}/api/mcp-connector/servers/{sid}/tools/", headers=auth)
            tool_list = tools if isinstance(tools, list) else (tools.get("results") or [])
            if any((t.get("tool_name") or t.get("name")) == "echo" for t in tool_list):
                break
            time.sleep(3)
    return server


def find_echo_target(auth: dict) -> tuple[str, str]:
    candidate = ensure_mcp_server(auth)
    slug = candidate.get("server_slug") or ""
    sid = candidate.get("id")
    code, tools = request("GET", f"{BASE}/api/mcp-connector/servers/{sid}/tools/", headers=auth)
    tool_list = tools if isinstance(tools, list) else (tools.get("results") or [])
    echo = next(
        (t for t in tool_list if (t.get("tool_name") or t.get("name")) == "echo"),
        tool_list[0] if tool_list else None,
    )
    if not echo:
        raise RuntimeError(f"No tools on server {slug}")
    tool_name = echo.get("tool_name") or echo.get("name") or "echo"
    return slug, tool_name


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = ""):
        results.append((name, ok, detail))
        status = "PASS" if ok else "FAIL"
        suffix = f" — {detail}" if detail else ""
        print(f"{status} {name}{suffix}")

    print("=== Step 1: API schema & contract ===")
    code, tok = request("POST", f"{BASE}/api/auth/token/", body={"email": EMAIL, "password": PASSWORD})
    check("auth_token", code == 200 and bool(tok.get("access")), f"HTTP {code}")
    if not tok.get("access"):
        return 1
    auth = {"Authorization": f"Bearer {tok['access']}"}

    code, baseline = request("GET", RISK_URL, headers=auth)
    check("GET mcp/risk", code == 200, f"HTTP {code}")
    for key in ("summary", "tool_ledger", "direction_split", "top_servers"):
        check(f"payload has {key}", key in baseline, "" if key in baseline else "missing")
    summary = baseline.get("summary") or {}
    for key in ("total_events", "blocked_tool_calls", "redacted_arguments", "unique_tools"):
        check(f"summary has {key}", key in summary, "" if key in summary else "missing")

    snap0 = summary_snapshot(baseline)
    print(
        f"Baseline: total_events={snap0['total_events']} blocked={snap0['blocked_tool_calls']} "
        f"redacted={snap0['redacted_arguments']} unique_tools={snap0['unique_tools']}"
    )

    print("\n=== Step 2: Dry-run isolation (negative check) ===")
    try:
        server_slug, tool_name = find_echo_target(auth)
    except RuntimeError as exc:
        check("resolve_mcp_target", False, str(exc))
        return 1

    dry_body = {
        "policy_domain": "mcp",
        "input_args": DRY_RUN_ARGS,
        "prompt": f"tool:{tool_name} {DRY_RUN_ARGS['message']}",
        "response": "",
        "metadata": {
            "tool_name": tool_name,
            "server_slug": server_slug,
            "event_type": "mcp_tool_call",
            "tools_invoked": [tool_name],
        },
    }
    code, dry = request("POST", f"{BASE}/api/policies/test/", headers=auth, body=dry_body)
    dry_action = dry.get("action", "")
    check(
        "dry_run_policy_hit",
        code == 200 and dry_action in ("block", "redact"),
        f"HTTP {code} action={dry_action}",
    )
    check("dry_run_flag", dry.get("dry_run") is True, f"dry_run={dry.get('dry_run')}")

    code, after_dry = request("GET", RISK_URL, headers=auth)
    snap1 = summary_snapshot(after_dry) if code == 200 else {}
    dry_delta = snap1.get("total_events", 0) - snap0["total_events"]
    check(
        "dry_run_does_not_increment_total_events",
        dry_delta == 0,
        f"delta={dry_delta} (CRITICAL telemetry isolation failure if non-zero)",
    )

    print("\n=== Step 3: Live telemetry integration ===")
    live_body = {
        "name": tool_name,
        "server_slug": server_slug,
        "arguments": LIVE_BLOCK_ARGS,
        "metadata": {
            "event_type": "mcp_tool_call",
            "tools_invoked": [tool_name],
            "server_slug": server_slug,
        },
    }
    code, live = request("POST", f"{BASE}/api/mcp-connector/tools/call/", headers=auth, body=live_body)
    live_decision = live.get("decision") or live.get("action") or ""
    check(
        "live_tool_call_recorded",
        code in (200, 400, 403) or live_decision in ("block", "redact", "allow"),
        f"HTTP {code} decision={live_decision} body={str(live)[:120]}",
    )

    print("Waiting 4s for backend task processing...")
    time.sleep(4)

    code, after_live = request("GET", RISK_URL, headers=auth)
    check("mcp_risk_refetch", code == 200, f"HTTP {code}")
    snap2 = summary_snapshot(after_live) if code == 200 else {}
    total_delta = snap2.get("total_events", 0) - snap1.get("total_events", snap0["total_events"])
    blocked_delta = snap2.get("blocked_tool_calls", 0) - snap1.get("blocked_tool_calls", snap0["blocked_tool_calls"])
    redacted_delta = snap2.get("redacted_arguments", 0) - snap1.get("redacted_arguments", snap0["redacted_arguments"])
    violation_delta = blocked_delta + redacted_delta

    check("total_events_incremented_by_1", total_delta == 1, f"delta={total_delta}")
    check("violation_counters_incremented_by_1", violation_delta == 1, f"blocked+redacted delta={violation_delta}")

    ledger_tools = {row.get("tool") for row in (after_live.get("tool_ledger") or [])}
    server_slugs = {row.get("server") for row in (after_live.get("top_servers") or [])}
    check("tool_ledger_contains_tool", tool_name in ledger_tools, f"ledger={sorted(ledger_tools)[:6]}")
    check("top_servers_contains_slug", server_slug in server_slugs, f"servers={sorted(server_slugs)[:6]}")

    failed = [r for r in results if not r[1]]
    print(f"\nSummary: passed={len(results) - len(failed)} failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
