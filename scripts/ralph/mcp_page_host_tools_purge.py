#!/usr/bin/env python3
"""Remove harness MCP servers (cp-host-* and cp-ht-*) from the org catalog."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8100"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"
PREFIXES = ("cp-host-", "cp-ht-")


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


def main():
    tok = login()
    _, lst = _req("GET", "/api/mcp-connector/servers/", tok=tok)
    removed = []
    for row in lst if isinstance(lst, list) else []:
        name = str(row.get("name", ""))
        if any(name.startswith(p) for p in PREFIXES):
            sid = row["id"]
            st, _ = _req("DELETE", f"/api/mcp-connector/servers/{sid}/", tok=tok)
            removed.append({"name": name, "delete_status": st})
    out = {"hostToolsPurgePass": True, "removed": removed, "count": len(removed)}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
