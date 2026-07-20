#!/usr/bin/env python3
"""MCP-page Ralph — Checkpoint 16 verify (client error sanitization).

Registers a failing streamable-http MCP (example.com/mcp → upstream 405 HTML)
and a failing stdio MCP (bad command → process exit code leak), triggers the
sync path, and asserts the CLIENT-facing message (sync response + persisted
last_sync_error) is a clean, branded, NON-revealing summary + a correlation ref
— NO raw upstream HTML, NO exit codes, NO server keys, NO "gateway logs" hints.
Direct control API (no browser) so it is deterministic under parallel-loop load.
"""
import json
import re
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8100"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"

# Anything in the client message matching these = a leak (CP16 fail).
# NOTE: the `(Ref: <hex>)` correlation id is stripped before scanning, so
# patterns here never match the ref's own hex/digits.
LEAK_PATTERNS = [
    r"<!doctype", r"<html", r"exited with code", r"exit code", r"code\s+-?\d+",
    r"proc\.", r"sandbox-agent", r"gateway logs", r"traceback", r"stderr",
    r"http 4\d\d:\s*<", r"stdout stream", r"host dependenc", r"'[^']*mcp[^']*'\s+failed",
]
REF_RE = re.compile(r"\(ref:\s*[0-9a-f]{6,}\)", re.I)


def _req(method, path, token=None, body=None, timeout=30):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw}


def login():
    st, body = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login failed {st}: {body}")
    return body["access"]


def collect_client_text(server_id, token, sync_resp):
    """The union of text a client could see for this failure."""
    texts = []
    if isinstance(sync_resp, dict):
        for k in ("detail", "error", "message", "last_sync_error"):
            v = sync_resp.get(k)
            if v:
                texts.append(str(v))
        if "_raw" in sync_resp:
            texts.append(str(sync_resp["_raw"]))
    st, srv = _req("GET", f"/api/mcp-connector/servers/{server_id}/", token=token)
    if isinstance(srv, dict) and srv.get("last_sync_error"):
        texts.append(str(srv["last_sync_error"]))
    return texts, srv


def check_case(name, create_body, token, results):
    st, created = _req("POST", "/api/mcp-connector/servers/", token=token, body=create_body)
    if st not in (200, 201):
        results[name] = {"pass": False, "why": f"create failed {st}: {created}"}
        return
    sid = created.get("id")
    # Trigger the sync/discovery path (client boundary).
    sst, sync_resp = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", token=token, timeout=60)
    texts, srv = collect_client_text(sid, token, sync_resp)
    joined = "\n".join(texts)
    low = joined.lower()
    has_ref = bool(REF_RE.search(low))
    # Strip the correlation ref so its hex/digits can't trip leak patterns.
    scan = REF_RE.sub("", low)
    leaks = [p for p in LEAK_PATTERNS if re.search(p, scan)]
    branded = any(s in low for s in (
        "the mcp server could not be", "the mcp server rejected",
        "the mcp server could not be started", "the mcp server did not respond",
        "the mcp server host is not permitted",
    ))
    ok = (not leaks) and has_ref and branded and bool(texts)
    results[name] = {
        "pass": ok, "sync_status": sst, "leaks": leaks,
        "has_ref": has_ref, "branded": branded,
        "client_text": (joined[:400] if joined else "(empty)"),
    }
    # cleanup
    _req("DELETE", f"/api/mcp-connector/servers/{sid}/", token=token)


def main():
    token = login()
    results = {}
    check_case("http_405", {
        "name": "cp16-http-fail", "transport": "streamable-http",
        "url": "https://example.com/mcp", "auth_type": "none",
    }, token, results)
    check_case("stdio_badcmd", {
        "name": "cp16-stdio-fail", "transport": "stdio",
        "command": "definitely-not-a-real-mcp-binary-xyz",
        "args": "[]", "auth_type": "none",
    }, token, results)

    all_pass = all(r.get("pass") for r in results.values())
    out = {"checkpoint": "16", "cp16Pass": all_pass, "cases": results}
    print(json.dumps(out, indent=2))
    print("CP16: PASS — client sees clean branded error + ref, no leakage" if all_pass else "CP16: FAIL")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
