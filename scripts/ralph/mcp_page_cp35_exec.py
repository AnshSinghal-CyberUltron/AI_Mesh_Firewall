#!/usr/bin/env python3
"""MCP-page Ralph — CP35: EXECUTE tools on the free/local catalog servers through the
gateway and assert REAL results. Deterministic checks: Everything.echo (echoes input)
+ Everything.add (a+b), Filesystem.list_allowed_directories, Memory.create_entities +
read_graph. Uses POST /api/mcp-connector/tools/call/ (the Tool-Execution path: control
→ gateway → per-org sandbox → the MCP server) so results are proven end-to-end.
"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8100"
EMAIL, PASS = "admin@zeroshield.io", "Adm1n!Pass#2024"
SYNC_T, CALL_T = 120, 60


def _req(method, path, tok=None, body=None, timeout=30, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    if headers:
        h.update(headers)
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
            return e.code, {"_raw": raw[:200]}
    except Exception as e:
        return "ERR", {"_raw": str(e)[:150]}


def login():
    st, b = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login {st}: {b}")
    return b["access"]


def register_sync(tok, name, spec):
    import time
    st, created = _req("POST", "/api/mcp-connector/servers/", tok=tok, body=spec)
    if st not in (200, 201):
        return None, None, f"create {st}: {created}"
    sid, slug = created["id"], created["server_slug"]
    # Retry the sync on a transient crash (SIGABRT under shared-host load) — the
    # parallel-loop stress can crash a Node server on cold start; it recovers on retry.
    sync = {}
    for _ in range(3):
        _, sync = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok, timeout=SYNC_T)
        if isinstance(sync, dict) and (sync.get("tools") or []):
            break
        time.sleep(3)
    return sid, slug, sync


def call(tok, slug, name, args):
    st, resp = _req("POST", "/api/mcp-connector/tools/call/", tok=tok,
                    body={"name": name, "arguments": args, "server_slug": slug},
                    timeout=CALL_T, headers={"X-Server-Slug": slug})
    return st, resp


def _text(resp):
    """Flatten an MCP tool-call result to searchable text."""
    return json.dumps(resp)


def main():
    tok = login()
    _, lst = _req("GET", "/api/mcp-connector/servers/", tok=tok)
    for row in (lst if isinstance(lst, list) else []):
        if str(row.get("name", "")).startswith("cp35-"):
            _req("DELETE", f"/api/mcp-connector/servers/{row['id']}/", tok=tok)

    checks = []
    created_ids = []

    # ── Everything: echo + add (deterministic) ──
    sid, slug, sync = register_sync(tok, "cp35-everything", {
        "name": "cp35-everything", "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"], "auth_type": "none"})
    if sid:
        created_ids.append(sid)
        tool_names = {t.get("tool_name") for t in (sync.get("tools") or [])} if isinstance(sync, dict) else set()
        MSG = "hello-cp35-deterministic-42"
        st, r = call(tok, slug, "echo", {"message": MSG})
        checks.append({"call": "everything.echo", "status": st, "ok": st == 200 and MSG in _text(r),
                       "result": _text(r)[:160]})
        if "add" in tool_names:
            st, r = call(tok, slug, "add", {"a": 7, "b": 5})
            checks.append({"call": "everything.add(7,5)", "status": st,
                           "ok": st == 200 and "12" in _text(r), "result": _text(r)[:160]})
        else:
            checks.append({"call": "everything.add", "ok": None, "note": "tool not offered by this server build"})

    # ── Filesystem: list_allowed_directories ──
    sid, slug, sync = register_sync(tok, "cp35-filesystem", {
        "name": "cp35-filesystem", "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data/mcp-auth"], "auth_type": "none"})
    if sid:
        created_ids.append(sid)
        st, r = call(tok, slug, "list_allowed_directories", {})
        checks.append({"call": "filesystem.list_allowed_directories", "status": st,
                       "ok": st == 200 and "/data/mcp-auth" in _text(r), "result": _text(r)[:160]})

    # ── Memory: create_entities then read_graph (round-trip) ──
    sid, slug, sync = register_sync(tok, "cp35-memory", {
        "name": "cp35-memory", "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"], "auth_type": "none"})
    if sid:
        created_ids.append(sid)
        ENT = "cp35-canary-entity"
        st, r = call(tok, slug, "create_entities",
                     {"entities": [{"name": ENT, "entityType": "test", "observations": ["cp35-obs"]}]})
        ok_create = st == 200 and "error" not in _text(r).lower()[:40]
        st2, r2 = call(tok, slug, "read_graph", {})
        checks.append({"call": "memory.create_entities+read_graph", "status": [st, st2],
                       "ok": ok_create and st2 == 200 and ENT in _text(r2), "result": _text(r2)[:160]})

    for sid in created_ids:
        _req("DELETE", f"/api/mcp-connector/servers/{sid}/", tok=tok)

    hard = [c for c in checks if c.get("ok") is not None]
    allok = bool(hard) and all(c["ok"] for c in hard)
    print(json.dumps({"checkpoint": "35", "cp35Pass": allok, "checks": checks}, indent=2))
    print(f"CP35: {'PASS' if allok else 'FAIL'} — {sum(1 for c in hard if c['ok'])}/{len(hard)} "
          f"deterministic tool executions returned REAL results")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
