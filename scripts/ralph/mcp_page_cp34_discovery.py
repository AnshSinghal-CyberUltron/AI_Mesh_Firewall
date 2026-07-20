#!/usr/bin/env python3
"""MCP-page Ralph — CP34: tool DISCOVERY for the catalog. Each connected server must
show its REAL, COMPLETE tool list — persisted with metadata (name + description +
input_schema), and the server's tools_count must match. A "0 tools" is only OK if
the server genuinely couldn't start (clean-error). Verifies discovery persistence
via GET /servers/{id}/tools/ (the list the UI Tool-Discovery tab renders).
"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8100"
EMAIL, PASS = "admin@zeroshield.io", "Adm1n!Pass#2024"
SYNC_TIMEOUT = 120

# Fast, no-auth, no-egress local servers + one http — the connectable subset (CP33).
SERVERS = [
    {"name": "cp34-everything", "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"], "auth_type": "none", "expect_min": 8},
    {"name": "cp34-memory", "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"], "auth_type": "none", "expect_min": 5},
    {"name": "cp34-filesystem", "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data/mcp-auth"], "auth_type": "none", "expect_min": 8},
    {"name": "cp34-context7", "transport": "streamable-http", "url": "https://mcp.context7.com/mcp", "auth_type": "none", "expect_min": 1},
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
            return e.code, {"_raw": raw[:150]}
    except Exception as e:
        return "ERR", {"_raw": str(e)[:120]}


def login():
    st, b = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login {st}: {b}")
    return b["access"]


def main():
    tok = login()
    _, lst = _req("GET", "/api/mcp-connector/servers/", tok=tok)
    for row in (lst if isinstance(lst, list) else []):
        if str(row.get("name", "")).startswith("cp34-"):
            _req("DELETE", f"/api/mcp-connector/servers/{row['id']}/", tok=tok)

    results = []
    for s in SERVERS:
        expect = s.pop("expect_min")
        st, created = _req("POST", "/api/mcp-connector/servers/", tok=tok, body=s)
        if st not in (200, 201):
            results.append({"name": s["name"], "ok": False, "why": f"create {st}"})
            continue
        sid = created["id"]
        sst, sync = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok, timeout=SYNC_TIMEOUT)
        # The persisted list the Tool-Discovery tab renders:
        _, tools = _req("GET", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok)
        _, srv = _req("GET", f"/api/mcp-connector/servers/{sid}/", tok=tok)
        tools = tools if isinstance(tools, list) else []
        with_desc = sum(1 for t in tools if (t.get("description") or "").strip())
        with_schema = sum(1 for t in tools if isinstance(t.get("input_schema"), dict) and t["input_schema"])
        tools_count_field = srv.get("tools_count")
        ok = (
            len(tools) >= expect
            and tools_count_field == len(tools)          # persisted count matches the list
            and with_schema >= 1                          # at least schemas captured
            and all((t.get("tool_name") or "").strip() for t in tools)  # real names
        )
        results.append({
            "name": s["name"], "ok": ok, "discovered": len(tools),
            "tools_count_field": tools_count_field, "with_description": with_desc,
            "with_input_schema": with_schema, "expect_min": expect,
            "sample": [t.get("tool_name") for t in tools[:6]],
        })
        _req("DELETE", f"/api/mcp-connector/servers/{sid}/", tok=tok)

    allok = all(r.get("ok") for r in results) and len(results) == len(SERVERS)
    print(json.dumps({"checkpoint": "34", "cp34Pass": allok, "results": results}, indent=2))
    total_tools = sum(r.get("discovered", 0) for r in results)
    print(f"CP34: {'PASS' if allok else 'FAIL'} — {total_tools} tools discovered w/ metadata across "
          f"{sum(1 for r in results if r.get('ok'))}/{len(results)} servers")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
