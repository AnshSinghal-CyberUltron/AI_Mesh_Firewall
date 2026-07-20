#!/usr/bin/env python3
"""Quick live gate: Semgrep + bogus MCP_HOST_TOOLS only (fast subset of cp33/host-tools)."""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8100"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"
TIMEOUT = 600


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


def login():
    st, b = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login {st}: {b}")
    return b["access"]


def purge(tok, prefix):
    _, lst = _req("GET", "/api/mcp-connector/servers/", tok=tok)
    removed = []
    for row in lst if isinstance(lst, list) else []:
        name = str(row.get("name", ""))
        if name.startswith(prefix):
            _req("DELETE", f"/api/mcp-connector/servers/{row['id']}/", tok=tok)
            removed.append(name)
    return removed


def register_sync(tok, body):
    st, created = _req("POST", "/api/mcp-connector/servers/", tok=tok, body=body)
    if st not in (200, 201):
        return {"pass": False, "stage": "create", "status": st, "detail": created}
    sid = created["id"]
    sst, sync = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok, timeout=TIMEOUT)
    tools = sync.get("tools") if isinstance(sync.get("tools"), list) else []
    err = (sync.get("last_sync_error") or "")[:300]
    code = sync.get("error_code") or ""
    return {
        "pass": None,
        "sync_status": sst,
        "connection_status": sync.get("connection_status"),
        "tools_count": len(tools),
        "error_code": code,
        "error_preview": err,
    }


def main():
    tok = login()
    purge(tok, "cp-ht-")
    purge(tok, "cp-host-")

    semgrep = register_sync(
        tok,
        {
            "name": "cp-ht-semgrep",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "mcp-server-semgrep"],
            "env_vars": {"MCP_HOST_TOOLS": "pip:semgrep"},
        },
    )
    semgrep["pass"] = (
        semgrep.get("connection_status") == "connected"
        and (semgrep.get("tools_count") or 0) > 0
    )

    bogus = register_sync(
        tok,
        {
            "name": "cp-ht-bogus",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-everything"],
            "env_vars": {"MCP_HOST_TOOLS": "pip:totally-fake-pkg-xyz-abc"},
        },
    )
    err = (bogus.get("error_preview") or "").lower()
    code = str(bogus.get("error_code") or "")
    bogus["pass"] = bogus.get("connection_status") == "failed" and (
        "MCP_HOST_TOOL" in code
        or "host tool" in err
        or "MCP_START" in code
    )

    cowsay = register_sync(
        tok,
        {
            "name": "cp-ht-cowsay",
            "transport": "stdio",
            "command": "python3",
            "args": ["-m", "agent.host_tools_demo_mcp"],
            "env_vars": {"MCP_HOST_TOOLS": "pip:cowsay"},
        },
    )
    cowsay["pass"] = (
        cowsay.get("connection_status") == "connected"
        and (cowsay.get("tools_count") or 0) > 0
    )

    out = {
        "hostToolsQuickPass": all(x.get("pass") for x in (semgrep, bogus, cowsay)),
        "semgrep": semgrep,
        "bogus": bogus,
        "cowsay": cowsay,
    }
    print(json.dumps(out, indent=2))
    return 0 if out["hostToolsQuickPass"] else 1


if __name__ == "__main__":
    sys.exit(main())
