#!/usr/bin/env python3
"""MCP-page Ralph — Checkpoint 21 verify (Ruflo MCP connects OR clean-errors).

Registers the real heavy Ruflo MCP (`npx -y ruflo@latest mcp start`) as a stdio
server and syncs it. PASS iff the outcome is EITHER:
  (a) connected + lists real tools (tools_count > 0, connection_status=connected), OR
  (b) a CLEAN D-error — error_code in the stable contract set, a correlation_id,
      NO raw leakage (exit codes / stderr / heap dumps), connection_status=failed,
      and the server is NOT shown as a connected 0-tools card.
NEVER a raw crash, a 0-tools "connected" card, or infinite loading. On the clean-
error branch it also confirms the staff dev-diagnostic captured the real cause.
"""
import json
import re
import subprocess
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8100"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"
CONTROL_CONTAINER = "ai_mesh_firewall-control-1"
D_CODES = {
    "MCP_AUTH_FAILED", "MCP_EGRESS_DENIED", "MCP_OUT_OF_MEMORY", "MCP_SERVER_CRASHED",
    "MCP_IMAGE_UNAVAILABLE", "MCP_INSUFFICIENT_STORAGE", "MCP_TIMEOUT", "MCP_START_FAILED", "MCP_UNAVAILABLE",
}
LEAK_PATTERNS = [
    r"<!doctype", r"<html", r"exited with code", r"code\s+-?\d+", r"proc\.",
    r"sandbox-agent", r"gateway logs", r"traceback", r"stderr", r"heap out of memory",
    r"node_modules", r"/root/", r"/app/",
]
REF_RE = re.compile(r"\(ref:\s*([0-9a-f]{6,})\)", re.I)


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
            return e.code, {"_raw": raw[:300]}


def login():
    st, b = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login failed {st}: {b}")
    return b["access"]


def main():
    tok = login()
    report = {"checkpoint": "21"}

    # Purge any prior cp21 rows.
    _, lst = _req("GET", "/api/mcp-connector/servers/", token=tok)
    for row in (lst if isinstance(lst, list) else []):
        if str(row.get("name", "")).startswith("cp21-ruflo"):
            _req("DELETE", f"/api/mcp-connector/servers/{row['id']}/", token=tok)

    st, created = _req("POST", "/api/mcp-connector/servers/", token=tok, body={
        "name": "cp21-ruflo", "transport": "stdio",
        # args is a JSONField → MUST be a real array (a JSON string would be
        # iterated char-by-char by the stdio spawner).
        "command": "npx", "args": ["-y", "ruflo@latest", "mcp", "start"],
        "auth_type": "none",
    })
    if st not in (200, 201):
        print(json.dumps({"cp21Pass": False, "why": f"create failed {st}: {created}"}, indent=2))
        sys.exit(1)
    sid = created["id"]

    # Sync — heavy install; allow a long but bounded window (the backend itself
    # bounds the stdio init, so a hang becomes a clean MCP_TIMEOUT, not infinite).
    sst, sync = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", token=tok, timeout=200)
    _, srv = _req("GET", f"/api/mcp-connector/servers/{sid}/", token=tok)

    tools = sync.get("tools") if isinstance(sync.get("tools"), list) else []
    err = str(sync.get("error") or "")
    code = sync.get("error_code")
    corr = sync.get("correlation_id")
    conn = sync.get("connection_status") or srv.get("connection_status")
    low = err.lower()
    scan = REF_RE.sub("", low)
    leaks = [p for p in LEAK_PATTERNS if re.search(p, scan)]

    connected_with_tools = (conn == "connected") and len(tools) > 0
    clean_error = (
        bool(err) and code in D_CODES and bool(corr)
        and not leaks and conn == "failed"
    )
    # Guard against the forbidden "connected 0-tools card".
    forbidden_zero_tools_card = (conn == "connected") and len(tools) == 0 and not err

    report.update({
        "sync_status": sst, "connection_status": conn, "tool_count": len(tools),
        "error_code": code, "correlation_id": corr, "error_msg": err[:200],
        "leaks": leaks, "connected_with_tools": connected_with_tools,
        "clean_error": clean_error, "forbidden_zero_tools_card": forbidden_zero_tools_card,
        "sample_tools": [t.get("tool_name") for t in tools[:8]],
    })

    # If it clean-errored, confirm the staff dev diagnostic captured the real cause.
    if clean_error and corr:
        try:
            mint = subprocess.run(
                ["docker", "exec", CONTROL_CONTAINER, "python", "/app/control/manage.py",
                 "shell", "-c",
                 "from django.core.cache import cache;import json;"
                 f"r=cache.get('mcp:diag:{corr}');print('DIAG:'+json.dumps(r) if r else 'DIAG:none')"],
                capture_output=True, text=True, timeout=60).stdout
            for line in mint.splitlines():
                if line.startswith("DIAG:"):
                    d = line[5:]
                    report["dev_diag_captured"] = d != "none"
                    report["dev_diag_raw_cause"] = (json.loads(d).get("raw_cause", "")[:160]
                                                    if d != "none" else None)
        except Exception as exc:  # noqa: BLE001
            report["dev_diag_error"] = str(exc)

    _req("DELETE", f"/api/mcp-connector/servers/{sid}/", token=tok)

    cp21_pass = (connected_with_tools or clean_error) and not forbidden_zero_tools_card and not leaks
    report["cp21Pass"] = cp21_pass
    print(json.dumps(report, indent=2))
    if connected_with_tools:
        print(f"CP21: PASS — Ruflo CONNECTED and listed {len(tools)} real tools")
    elif clean_error:
        print(f"CP21: PASS — Ruflo clean-errored ({code}) with correlation id, no raw crash/leak")
    else:
        print("CP21: FAIL")
    sys.exit(0 if cp21_pass else 1)


if __name__ == "__main__":
    main()
