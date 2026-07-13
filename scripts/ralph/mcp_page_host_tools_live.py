#!/usr/bin/env python3
"""Live proof: MCP_HOST_TOOLS on-demand CLI install (general mechanism).

Registers three stdio servers:
  1. cp-host-semgrep — Semgrep MCP + pip:semgrep (should connect)
  2. cp-host-cowsay — trivial stub shelling cowsay (proves general CLI path)
  3. cp-host-bogus — fake package (clean MCP_HOST_TOOL_FAILED / start error)

Prerequisites: docker stack up; sandbox image rebuilt with host-tools wiring.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8100"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"
SYNC_TIMEOUT = 180

CASES = [
    {
        "name": "cp-host-semgrep",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "mcp-server-semgrep"],
        "env_vars": {"MCP_HOST_TOOLS": "pip:semgrep"},
        "expect": "connected",
    },
    {
        "name": "cp-host-cowsay",
        "transport": "stdio",
        "command": "python3",
        "args": ["-m", "agent.host_tools_demo_mcp"],
        "env_vars": {"MCP_HOST_TOOLS": "pip:cowsay"},
        "expect": "connected",
    },
    {
        "name": "cp-host-bogus",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"],
        "env_vars": {"MCP_HOST_TOOLS": "pip:totally-fake-pkg-xyz-abc"},
        "expect": "host_tool_or_start_fail",
    },
]


def _req(method, path, tok=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw[:300]}
    except Exception as e:
        return "ERR", {"_raw": str(e)[:200]}


def login():
    st, b = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login {st}: {b}")
    return b["access"]


def main():
    tok = login()
    _, lst = _req("GET", "/api/mcp-connector/servers/", tok=tok)
    for row in lst if isinstance(lst, list) else []:
        name = str(row.get("name", ""))
        if name.startswith("cp-host-") or name.startswith("cp-ht-"):
            _req("DELETE", f"/api/mcp-connector/servers/{row['id']}/", tok=tok)

    results = []
    ok = True
    for case in CASES:
        st, created = _req("POST", "/api/mcp-connector/servers/", tok=tok, body=case)
        rec = {"name": case["name"], "create_status": st}
        if st not in (200, 201):
            rec["pass"] = False
            results.append(rec)
            ok = False
            continue
        sid = created["id"]
        sst, sync = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok, timeout=SYNC_TIMEOUT)
        conn = sync.get("connection_status")
        tools = sync.get("tools") if isinstance(sync.get("tools"), list) else []
        err = (sync.get("last_sync_error") or sync.get("error") or "")[:200]
        code = sync.get("error_code") or ""
        rec.update({
            "sync_status": sst,
            "connection_status": conn,
            "tools_count": len(tools),
            "error_code": code,
            "error_preview": err,
        })
        if case["expect"] == "connected":
            rec["pass"] = conn == "connected" and len(tools) > 0
        elif case["expect"] == "host_tool_installed":
            rec["pass"] = "host tool install failed" not in err.lower()
        elif case["expect"] == "host_tool_or_start_fail":
            rec["pass"] = conn == "failed" and (
                "MCP_HOST_TOOL" in str(code)
                or "host tool" in err.lower()
                or "MCP_START" in str(code)
            )
        else:
            rec["pass"] = False
        if not rec["pass"]:
            ok = False
        results.append(rec)

    out = {"hostToolsLivePass": ok, "results": results}
    print(json.dumps(out, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
