#!/usr/bin/env python3
"""MCP-page Ralph — Checkpoint 18 verify (developer diagnostic channel).

Proves the staff-only diagnostic channel:
  1. Register a failing MCP → client gets sanitized message + code + correlation_id.
  2. STAFF user GET /diagnostics/<ref>/ → 200 with the REAL raw cause (raw_cause,
     code, org/server) that the client message deliberately withholds.
  3. NON-STAFF user (an org admin) GET same → 403 (never exposed to clients).
  4. Invalid ref → 400; unknown ref → 404.
Direct control API.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8100"
EMAIL = "admin@zeroshield.io"          # staff + superuser
PASS = "Adm1n!Pass#2024"
CONTROL_CONTAINER = os.environ.get("CONTROL_CONTAINER", "ai_mesh_firewall-control-1")


def mint_nonstaff_token():
    """Mint a valid JWT for a NON-staff org admin (to prove the 403 lockout)
    without persisting any credential to disk. Best-effort; returns "" on failure.
    """
    code = (
        "from django.contrib.auth import get_user_model;"
        "from rest_framework_simplejwt.tokens import RefreshToken;"
        "U=get_user_model();u=U.objects.filter(is_staff=False).first();"
        "print('TOKEN:'+str(RefreshToken.for_user(u).access_token)) if u else print('TOKEN:')"
    )
    try:
        out = subprocess.run(
            ["docker", "exec", CONTROL_CONTAINER, "python",
             "/app/control/manage.py", "shell", "-c", code],
            capture_output=True, text=True, timeout=60,
        ).stdout
        for line in out.splitlines():
            if line.startswith("TOKEN:"):
                return line[len("TOKEN:"):].strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] could not mint non-staff token: {exc}", file=sys.stderr)
    return ""


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


def main():
    staff = login()
    nonstaff = mint_nonstaff_token()
    report = {"checkpoint": "18", "steps": {}}

    # 0) Purge any orphan cp18-diag rows a prior interrupted run may have left,
    #    so the create below never 400s on a duplicate name.
    _, existing = _req("GET", "/api/mcp-connector/servers/", token=staff)
    for row in (existing if isinstance(existing, list) else []):
        if str(row.get("name", "")).startswith("cp18-diag"):
            _req("DELETE", f"/api/mcp-connector/servers/{row.get('id')}/", token=staff)

    # 1) Register a failing http server + trigger sync → capture correlation_id.
    st, created = _req("POST", "/api/mcp-connector/servers/", token=staff, body={
        "name": "cp18-diag", "transport": "streamable-http",
        "url": "https://example.com/mcp", "auth_type": "none",
    })
    sid = created.get("id")
    sst, sync = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", token=staff, timeout=60)
    ref = sync.get("correlation_id")
    client_msg = str(sync.get("error") or "")
    report["steps"]["register_and_sync"] = {
        "correlation_id": ref, "error_code": sync.get("error_code"),
        "client_msg": client_msg[:180],
        "client_has_no_rawcause": "raw_cause" not in client_msg and "<!doctype" not in client_msg.lower(),
    }

    # 2) STAFF lookup → 200 with the real raw cause.
    dst, diag = _req("GET", f"/api/mcp-connector/diagnostics/{ref}/", token=staff)
    staff_ok = (
        dst == 200 and isinstance(diag, dict)
        and diag.get("ref") == ref and diag.get("code") == sync.get("error_code")
        and bool(diag.get("raw_cause"))
        and diag.get("server_slug") is not None
    )
    report["steps"]["staff_lookup"] = {
        "status": dst, "pass": staff_ok,
        "code": diag.get("code"), "server_slug": diag.get("server_slug"),
        "raw_cause_sample": str(diag.get("raw_cause", ""))[:160],
        "raw_reveals_realcause": bool(re.search(r"http|405|<|exit|refused|error", str(diag.get("raw_cause", "")).lower())),
    }

    # 3) NON-STAFF (org admin) lookup → 403 (never exposed to clients).
    nst, ndiag = _req("GET", f"/api/mcp-connector/diagnostics/{ref}/", token=nonstaff) if nonstaff else (None, {})
    nonstaff_locked_out = nst in (401, 403)
    report["steps"]["nonstaff_lookup"] = {
        "status": nst, "pass": nonstaff_locked_out,
        "leaked_rawcause": isinstance(ndiag, dict) and "raw_cause" in ndiag,
    }

    # 4) Edge: invalid ref → 400; unknown-but-valid ref → 404.
    ist, _ = _req("GET", "/api/mcp-connector/diagnostics/NOT-a-ref!!/", token=staff)
    ust, _ = _req("GET", "/api/mcp-connector/diagnostics/deadbeef0000/", token=staff)
    report["steps"]["edge_cases"] = {
        "invalid_ref_status": ist, "unknown_ref_status": ust,
        "pass": ist == 400 and ust == 404,
    }

    # cleanup
    _req("DELETE", f"/api/mcp-connector/servers/{sid}/", token=staff)

    cp18_pass = (
        report["steps"]["register_and_sync"]["client_has_no_rawcause"]
        and bool(ref)
        and staff_ok
        and report["steps"]["staff_lookup"]["raw_reveals_realcause"]
        and nonstaff_locked_out
        and not report["steps"]["nonstaff_lookup"]["leaked_rawcause"]
        and report["steps"]["edge_cases"]["pass"]
    )
    report["cp18Pass"] = cp18_pass
    print(json.dumps(report, indent=2))
    print("CP18: PASS — staff sees real cause via ref; client + non-staff never do; edges correct"
          if cp18_pass else "CP18: FAIL")
    sys.exit(0 if cp18_pass else 1)


if __name__ == "__main__":
    main()
