#!/usr/bin/env python3
"""MCP-page cleanup item 06 — LIVE verify: every failing server shows a CLEAN,
non-revealing error (no raw exc / upstream HTML / internal hostname / exit code /
'sandbox-agent logs'). Drives the real control→gateway path on my org (zeroshield).

Cases:
  * streamable-http to a NON-RESOLVING host  → DNS failure → clean "could not reach"
  * stdio with a bogus package/command       → start failure → clean, no exit code
Reads each server's last_sync_error after sync and asserts it is brand-safe.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8100"
EMAIL, PASS = "admin@zeroshield.io", "Adm1n!Pass#2024"

# Substrings that would betray a raw leak in a client-facing sync error.
LEAK = [
    "traceback", "<html", "nginx", "getaddrinfo", "errno", "name or service",
    "exit code", "signal ", "sigabrt", "sigsegv", "sandbox-agent", "stderr",
    "mcp_sandbox", "node_modules", "connecterror", "httpx.", "0x",
]


def _req(method, path, tok=None, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw[:200]}
    except Exception as e:  # noqa: BLE001
        return "ERR", {"_raw": str(e)[:160]}


def login():
    st, b = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login {st}: {b}")
    return b["access"]


def _is_clean(msg: str) -> tuple[bool, list]:
    low = (msg or "").lower()
    hits = [tok for tok in LEAK if tok in low]
    # a bare exit-code integer like -9 / 137 / -6 must not appear either
    if re.search(r"(?<![\w.])-?(?:9|11|6|137|134|139)\b(?!\w)", low) and (
        "exit" in low or "code" in low or "signal" in low):
        hits.append("exit-number")
    return (not hits), hits


def register_and_sync(tok, payload, label):
    # purge same-named orphans first
    _, lst = _req("GET", "/api/mcp-connector/servers/", tok=tok)
    for row in (lst if isinstance(lst, list) else []):
        if str(row.get("name", "")) == payload["name"]:
            _req("DELETE", f"/api/mcp-connector/servers/{row['id']}/", tok=tok)
    st, c = _req("POST", "/api/mcp-connector/servers/", tok=tok, body=payload)
    if st not in (200, 201) or "id" not in c:
        return {"label": label, "ok": False, "err": f"register {st}: {c}"}
    sid = c["id"]
    last = ""
    for _ in range(4):
        _, sync = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok, timeout=120)
        # the sync response and/or the server row carry the sanitized error
        _, row = _req("GET", f"/api/mcp-connector/servers/{sid}/", tok=tok)
        last = (row.get("last_sync_error") or sync.get("error") or sync.get("detail")
                or json.dumps(sync))[:400]
        code = row.get("last_sync_error_code") or sync.get("code")
        ref = row.get("last_sync_error_ref") or sync.get("ref")
        if last and last != "{}":
            break
        time.sleep(2)
    clean, hits = _is_clean(last)
    _req("DELETE", f"/api/mcp-connector/servers/{sid}/", tok=tok)
    return {"label": label, "ok": clean, "leaks": hits, "code": code, "ref": ref,
            "message": last[:200]}


def main():
    tok = login()
    results = []
    # 1) streamable-http to a non-resolving host → DNS failure
    results.append(register_and_sync(tok, {
        "name": "item06-dnsfail", "transport": "streamable-http",
        "url": "https://nonexistent-mcp-host-zzz-9931.example.invalid/mcp",
        "auth_type": "none"}, "streamable-http DNS-fail"))
    # 2) stdio with a bogus npx package → start failure (rc!=0), no exit-code leak
    results.append(register_and_sync(tok, {
        "name": "item06-badstdio", "transport": "stdio", "command": "npx",
        "args": ["-y", "@zeroshield/definitely-not-a-real-package-zzz-9931"],
        "auth_type": "none"}, "stdio bad-package start-fail"))

    allclean = all(r["ok"] for r in results)
    print(json.dumps({"item06_clean_errors": allclean, "results": results}, indent=2))
    print("ITEM06:", "PASS — every failing server shows a clean, non-revealing error"
          if allclean else "FAIL — a leak survived")
    return 0 if allclean else 1


if __name__ == "__main__":
    sys.exit(main())
