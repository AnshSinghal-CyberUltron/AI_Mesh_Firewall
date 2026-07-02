#!/usr/bin/env python3
"""MCP-page Ralph — CP37: every per-server action on the MCP Servers list works AND
reflects state. Exercises the exact backend each card button drives:
  connect/sync  → POST /servers/{id}/tools/        (tools discovered)
  details       → GET  /servers/{id}/tools/         (tool list)
  disable       → PATCH /servers/{id}/tools/{name}/ {enabled:false}  → persisted
  scan          → PATCH /servers/{id}/tools/{name}/ {scan_action:redact} → persisted
  authorize     → POST /servers/{id}/oauth/authorize/ (returns an authorize URL or clean error)
  delete        → DELETE /servers/{id}/              → GET now 404
Each mutation is READ BACK to prove the state reflects the action.
"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8100"
EMAIL, PASS = "admin@zeroshield.io", "Adm1n!Pass#2024"


def _req(method, path, tok=None, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
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
    checks = {}
    _, lst = _req("GET", "/api/mcp-connector/servers/", tok=tok)
    for row in (lst if isinstance(lst, list) else []):
        if str(row.get("name", "")).startswith("cp37-"):
            _req("DELETE", f"/api/mcp-connector/servers/{row['id']}/", tok=tok)

    # ── connect/sync + details on a real (Everything) server ──
    st, c = _req("POST", "/api/mcp-connector/servers/", tok=tok, body={
        "name": "cp37-everything", "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"], "auth_type": "none"})
    sid = c["id"]
    for _ in range(3):
        _, sync = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok, timeout=120)
        if (sync.get("tools") or []):
            break
        import time; time.sleep(3)
    _, tools = _req("GET", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok)
    tools = tools if isinstance(tools, list) else []
    checks["connect_sync"] = {"ok": len(tools) > 0, "tools": len(tools)}
    checks["details"] = {"ok": len(tools) > 0 and all(t.get("tool_name") for t in tools)}

    if tools:
        tname = tools[0]["tool_name"]
        # ── disable action ──
        _, _ = _req("PATCH", f"/api/mcp-connector/servers/{sid}/tools/{tname}/", tok=tok, body={"enabled": False})
        _, after = _req("GET", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok)
        d = next((t for t in after if t.get("tool_name") == tname), {})
        checks["disable"] = {"ok": d.get("enabled") is False, "tool": tname}
        # re-enable
        _req("PATCH", f"/api/mcp-connector/servers/{sid}/tools/{tname}/", tok=tok, body={"enabled": True})
        # ── scan action (per-tool scan_action) ──
        _, _ = _req("PATCH", f"/api/mcp-connector/servers/{sid}/tools/{tname}/", tok=tok, body={"scan_action": "redact"})
        _, after2 = _req("GET", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok)
        d2 = next((t for t in after2 if t.get("tool_name") == tname), {})
        checks["scan"] = {"ok": d2.get("scan_action") == "redact", "tool": tname}
        _req("PATCH", f"/api/mcp-connector/servers/{sid}/tools/{tname}/", tok=tok, body={"scan_action": "inherit"})

    # ── authorize action (OAuth start on an http-oauth server) ──
    st_o, co = _req("POST", "/api/mcp-connector/servers/", tok=tok, body={
        "name": "cp37-oauth", "transport": "streamable-http",
        "url": "https://api.githubcopilot.com/mcp/", "auth_type": "oauth"})
    if st_o in (200, 201):
        oid = co["id"]
        ast, ar = _req("POST", f"/api/mcp-connector/servers/{oid}/oauth/authorize/", tok=tok, timeout=45)
        # WORKS = returns an authorize/authorization URL, OR a clean discovery error (not a crash/500).
        has_url = isinstance(ar, dict) and any(k in ar for k in ("authorize_url", "authorization_url", "url", "auth_url"))
        clean_err = ast in (400, 502) and isinstance(ar, dict) and ("error" in ar or "detail" in ar)
        checks["authorize"] = {"ok": (ast == 200 and has_url) or clean_err, "status": ast,
                               "keys": list(ar.keys())[:5] if isinstance(ar, dict) else None}
        _req("DELETE", f"/api/mcp-connector/servers/{oid}/", tok=tok)
    else:
        checks["authorize"] = {"ok": False, "why": f"oauth-server create {st_o}: {co}"}

    # ── delete action + state reflects (404 after) ──
    dst, _ = _req("DELETE", f"/api/mcp-connector/servers/{sid}/", tok=tok)
    gst, _ = _req("GET", f"/api/mcp-connector/servers/{sid}/", tok=tok)
    checks["delete"] = {"ok": dst in (200, 204) and gst == 404, "delete_status": dst, "get_after": gst}

    allok = all(v.get("ok") for v in checks.values())
    print(json.dumps({"checkpoint": "37", "cp37Pass": allok, "checks": checks}, indent=2))
    print(f"CP37: {'PASS' if allok else 'FAIL'} — "
          f"{sum(1 for v in checks.values() if v.get('ok'))}/{len(checks)} per-server actions work + reflect state")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
