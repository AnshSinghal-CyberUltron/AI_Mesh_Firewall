#!/usr/bin/env python3
"""MCP-page cleanup item 21 — RE-TEST the fleet: connected servers work; failed ones
show CLEAN, non-revealing errors (no raw leak); the OOM-heavy one (ruflo) shows a clean
memory error.

Re-syncs EVERY server through the now-deployed clean-error path (POST /servers/<id>/tools/
→ _resync_server_tools), then asserts:
  - connected servers have tools_count > 0 (they work),
  - EVERY failed server's last_sync_error is CLEAN — contains NONE of the raw-leak
    markers (exit codes, signals, Errno/getaddrinfo, HTML, internal 'org/slug' keys,
    'Upstream MCP error'/'discovery failed', 'sandbox-agent', 'gateway logs', env-var
    names, raw IPs) — and reads as a branded, human message (ideally with a Ref),
  - ruflo is connected OR shows a clean memory/limit message.

Why this matters: two servers (cp09-verify, SSE Everything stub) carried STALE
last_sync_error strings written BEFORE the clean-error fixes deployed — the re-sync
must refresh them to clean branded errors.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8100"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"

# Raw-leak markers that must NEVER appear in a client-facing last_sync_error.
LEAK_PATTERNS = [
    r"exit(ed)?\s*(with)?\s*code",       # "exited with code -6"
    r"code\s*-?\d",                       # "code -6", "code 137"
    r"signal\s*\d",
    r"\bSIG[A-Z]+\b",
    r"\[?Errno",                          # "[Errno -2]"
    r"getaddrinfo",
    r"Name or service not known",
    r"<!?\s*doctype|<html|</html>|<body|nginx",  # HTML page
    r"Upstream MCP error",
    r"Upstream discovery failed",
    r"zeroshield/",                       # internal org/slug key
    r"sandbox-agent",
    r"gateway logs|See gateway logs",
    r"MCP_SANDBOX|AGENT_API_KEY|GATEWAY_INTERNAL",
    r"\b\d{1,3}(\.\d{1,3}){3}\b",         # raw IPv4 (169.254.169.254 etc.)
    r"Traceback|File \"",
]
# A clean error should read like a branded sentence (heuristic positive signal).
BRANDED_HINTS = [
    "The MCP server", "needs re-authentication", "could not be reached",
    "ran out of memory", "rejected authentication", "could not be started",
    "re-authorize", "Verify the configuration",
]


def _req(method, path, headers, body=None, timeout=45):
    data = json.dumps(body).encode() if body is not None else None
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
        return -1, {"_err": str(e)}


def login():
    st, b = _req("POST", "/api/auth/token/", {"Content-Type": "application/json"},
                 {"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login failed {st}: {b}")
    return b["access"]


def servers(tok):
    st, d = _req("GET", "/api/mcp-connector/servers/", {"Authorization": "Bearer " + tok})
    if st != 200:
        raise SystemExit(f"servers list failed {st}")
    return d if isinstance(d, list) else d.get("results", [])


def find_leaks(msg):
    return [p for p in LEAK_PATTERNS if re.search(p, msg or "", re.IGNORECASE)]


def main():
    tok = login()
    hdr = {"Authorization": "Bearer " + tok, "Content-Type": "application/json"}
    fleet = servers(tok)
    report = {"resynced": 0, "servers": [], "dirty": [], "connected_no_tools": []}

    # Re-sync every server through the clean-error path (refreshes stale errors).
    for s in fleet:
        st, _ = _req("POST", f"/api/mcp-connector/servers/{s['id']}/tools/", hdr, {}, timeout=60)
        if st in (200, 201, 202):
            report["resynced"] += 1
    time.sleep(3)  # allow any async settle

    fleet = servers(tok)
    for s in fleet:
        name = s.get("name")
        status = s.get("connection_status")
        tools = int(s.get("tools_count") or 0)
        err = s.get("last_sync_error") or ""
        row = {"name": name, "status": status, "tools": tools}
        if status == "connected":
            row["works"] = tools > 0
            if tools == 0:
                report["connected_no_tools"].append(name)
        else:
            leaks = find_leaks(err)
            branded = any(h.lower() in err.lower() for h in BRANDED_HINTS)
            has_ref = bool(re.search(r"\(Ref: [0-9a-f]{6,}\)", err)) or "re-authentication" in err or "ran out of memory" in err
            row["error"] = err[:160]
            row["leaks"] = leaks
            row["branded"] = branded
            row["clean"] = (not leaks) and branded
            if leaks or not branded:
                report["dirty"].append({"name": name, "error": err[:200], "leaks": leaks})
        report["servers"].append(row)

    conn = [s for s in fleet if s.get("connection_status") == "connected"]
    failed = [s for s in fleet if s.get("connection_status") != "connected"]
    ruflo = next((s for s in fleet if (s.get("name") or "").lower() == "ruflo"), None)
    ruflo_ok = ruflo is None or ruflo.get("connection_status") == "connected" or (
        "memory" in (ruflo.get("last_sync_error") or "").lower()
        or "limit" in (ruflo.get("last_sync_error") or "").lower()
    )

    report["summary"] = {
        "total": len(fleet), "connected": len(conn), "failed": len(failed),
        "connected_all_have_tools": len(report["connected_no_tools"]) == 0,
        "all_failed_clean": len(report["dirty"]) == 0,
        "ruflo_ok": ruflo_ok,
    }
    report["item21Pass"] = (
        report["summary"]["connected_all_have_tools"]
        and report["summary"]["all_failed_clean"]
        and ruflo_ok
        and len(conn) > 0
    )

    print(json.dumps(report, indent=2))
    print(f"ITEM-21: {'PASS' if report['item21Pass'] else 'FAIL'} — {len(conn)} connected "
          f"(all with tools={report['summary']['connected_all_have_tools']}), {len(failed)} failed "
          f"(all clean={report['summary']['all_failed_clean']}), ruflo_ok={ruflo_ok}"
          + ("" if report["item21Pass"] else f"; DIRTY={[d['name'] for d in report['dirty']]}"))
    sys.exit(0 if report["item21Pass"] else 1)


if __name__ == "__main__":
    main()
