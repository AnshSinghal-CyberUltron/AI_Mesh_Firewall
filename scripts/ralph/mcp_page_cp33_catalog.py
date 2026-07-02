#!/usr/bin/env python3
"""MCP-page Ralph — CP33: connect the catalog MCPs via the register flow (the same
control-plane backend the UI modal drives), the 10 PRIORITY servers first. For each:
register (POST /servers/) → sync (POST /servers/{id}/tools/) → capture outcome
{connection_status, tools_count, error_code}. A server is OK if it CONNECTS + lists
real tools OR clean-errors with a stable D-code (never a raw crash/hang). Records the
matrix for CP34 (discovery) / CP35 (execution) / CP36 (triage).
"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8100"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"
SYNC_TIMEOUT = 110

# The 10 priority presets (frontend MCP_PRESETS). args = real JSON arrays.
PRESETS = [
    {"name": "cp33-github", "transport": "streamable-http", "url": "https://api.githubcopilot.com/mcp/", "auth_type": "bearer"},
    {"name": "cp33-linear", "transport": "stdio", "command": "npx", "args": ["-y", "mcp-remote", "https://mcp.linear.app/mcp"], "auth_type": "none"},
    {"name": "cp33-context7", "transport": "streamable-http", "url": "https://mcp.context7.com/mcp", "auth_type": "none"},
    {"name": "cp33-playwright", "transport": "stdio", "command": "npx", "args": ["-y", "@playwright/mcp@latest"], "auth_type": "none"},
    {"name": "cp33-semgrep", "transport": "stdio", "command": "npx", "args": ["-y", "mcp-server-semgrep"], "auth_type": "none"},
    {"name": "cp33-memory", "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"], "auth_type": "none"},
    {"name": "cp33-filesystem", "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data/mcp-auth"], "auth_type": "none"},
    {"name": "cp33-fetch", "transport": "stdio", "command": "npx", "args": ["-y", "mcp-server-fetch"], "auth_type": "none"},
    {"name": "cp33-everything", "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"], "auth_type": "none"},
    {"name": "cp33-vibecheck", "transport": "stdio", "command": "npx", "args": ["-y", "@pv-bhat/vibe-check-mcp", "start", "--stdio"], "auth_type": "none"},
]
D_CODES = {"MCP_AUTH_FAILED", "MCP_EGRESS_DENIED", "MCP_OUT_OF_MEMORY", "MCP_SERVER_CRASHED",
           "MCP_IMAGE_UNAVAILABLE", "MCP_INSUFFICIENT_STORAGE", "MCP_TIMEOUT", "MCP_START_FAILED", "MCP_UNAVAILABLE"}


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
            return e.code, {"_raw": raw[:200]}
    except Exception as e:
        return "ERR", {"_raw": str(e)[:120]}


def login():
    st, b = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login {st}: {b}")
    return b["access"]


def main():
    tok = login()
    # purge prior cp33 rows
    _, lst = _req("GET", "/api/mcp-connector/servers/", tok=tok)
    for row in (lst if isinstance(lst, list) else []):
        if str(row.get("name", "")).startswith("cp33-"):
            _req("DELETE", f"/api/mcp-connector/servers/{row['id']}/", tok=tok)

    results = []
    for p in PRESETS:
        st, created = _req("POST", "/api/mcp-connector/servers/", tok=tok, body=p)
        if st not in (200, 201):
            results.append({"name": p["name"], "outcome": "create_failed", "status": st, "detail": str(created)[:120]})
            continue
        sid = created["id"]
        sst, sync = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok, timeout=SYNC_TIMEOUT)
        tools = sync.get("tools") if isinstance(sync.get("tools"), list) else []
        conn = sync.get("connection_status")
        code = sync.get("error_code")
        connected = conn == "connected" and len(tools) > 0
        clean_err = bool(sync.get("error")) and code in D_CODES and conn == "failed"
        results.append({
            "name": p["name"], "transport": p["transport"],
            "connection_status": conn, "tool_count": len(tools),
            "error_code": code,
            "outcome": "CONNECTED" if connected else ("clean_error" if clean_err else "BROKEN"),
            "sample_tools": [t.get("tool_name") for t in tools[:5]],
        })
        _req("DELETE", f"/api/mcp-connector/servers/{sid}/", tok=tok)

    connected = [r for r in results if r.get("outcome") == "CONNECTED"]
    clean = [r for r in results if r.get("outcome") == "clean_error"]
    broken = [r for r in results if r.get("outcome") not in ("CONNECTED", "clean_error")]
    out = {"checkpoint": "33", "total": len(results),
           "connected": len(connected), "clean_error": len(clean), "broken": len(broken),
           "results": results}
    print(json.dumps(out, indent=2))
    # PASS = none left BROKEN (every server connects OR clean-errors).
    cp33_pass = len(broken) == 0 and len(results) == len(PRESETS)
    print(f"CP33: {'PASS' if cp33_pass else 'NEEDS-TRIAGE'} — {len(connected)} connected, "
          f"{len(clean)} clean-error, {len(broken)} broken")
    sys.exit(0 if cp33_pass else 1)


if __name__ == "__main__":
    main()
