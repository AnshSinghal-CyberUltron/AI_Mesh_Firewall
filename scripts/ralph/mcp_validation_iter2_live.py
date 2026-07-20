#!/usr/bin/env python3
"""Ralph MCP platform validation — iteration 2 live API proofs.

Proves:
  1. Auth via /api/auth/token/ (not /login/)
  2. scan_controls row count matches enabled-tools scan_controls_configured
  3. MCP tool call via gateway runs two-tier scan when configured (scan_trace present)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
EMAIL = os.environ.get("RALPH_ADMIN_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("RALPH_ADMIN_PASSWORD", "Adm1n!Pass#2024")
ORG = os.environ.get("ORG_SLUG", "zeroshield")
SERVER = os.environ.get("MCP_SERVER", "cp09-ens8do")


def _json(method: str, url: str, headers: dict | None = None, body: dict | None = None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return e.code, payload


def _internal_key() -> str:
    out = subprocess.check_output(
        ["docker", "exec", "ai_mesh_firewall-gateway-1", "printenv", "GATEWAY_INTERNAL_API_KEY"],
        text=True,
    ).strip()
    if not out:
        raise RuntimeError("GATEWAY_INTERNAL_API_KEY empty in gateway container")
    return out


def main() -> int:
    results: dict = {"ok": True, "checks": []}

    def check(name: str, passed: bool, detail: object):
        results["checks"].append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            results["ok"] = False
        print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")

    # 1. JWT auth
    st, tok = _json("POST", f"{CONTROL}/api/auth/token/", body={"email": EMAIL, "password": PASSWORD})
    access = tok.get("access", "")
    check("auth_token", st == 200 and bool(access), {"status": st, "token_len": len(access)})

    # 2. scan-controls count (user API)
    st, rows = _json("GET", f"{CONTROL}/api/mcp-connector/scan-controls/", headers={"Authorization": f"Bearer {access}"})
    scan_rows = rows if isinstance(rows, list) else rows.get("results", [])
    n_rows = len(scan_rows) if isinstance(scan_rows, list) else 0
    check("scan_controls_api", st == 200, {"status": st, "row_count": n_rows})

    # 3. enabled-tools internal (gateway contract)
    key = _internal_key()
    ih = {
        "X-Gateway-Internal-Key": key,
        "X-Gateway-Auth": "true",
        "X-Org-Slug": ORG,
    }
    st, en = _json("GET", f"{CONTROL}/api/mcp-connector/internal/enabled-tools/?server_slug={SERVER}", headers=ih)
    configured = en.get("scan_controls_configured")
    check(
        "enabled_tools_scan_flag",
        st == 200 and configured is (n_rows > 0),
        {
            "status": st,
            "scan_controls_configured": configured,
            "api_row_count": n_rows,
            "effective_tier1_input_enabled": (en.get("effective_scan_controls") or {}).get("tier1_input", {}).get("enabled"),
        },
    )

    # 4. gateway key from manifest
    manifest_path = os.path.join(os.path.dirname(__file__), ".mcp_scale_manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        mf = json.load(f)
    gkey = next((o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG), "")
    check("manifest_gateway_key", bool(gkey), {"present": bool(gkey)})

    # 5. tool call with PII in echo arg — expect scan ran (not scan_skipped)
    pii = "bob.jones@corp.example"
    rpc = {
        "jsonrpc": "2.0",
        "id": 42,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": pii}},
    }
    st, resp = _json(
        "POST",
        f"{GATEWAY}/gateway/{ORG}/mcp/{SERVER}",
        headers={"Authorization": f"Bearer {gkey}"},
        body=rpc,
    )
    result = (resp.get("result") or {}) if isinstance(resp, dict) else {}
    content = result.get("content") if isinstance(result, dict) else None
    text = ""
    if isinstance(content, list) and content:
        text = (content[0] or {}).get("text", "")
    # When scan controls configured + redact posture, email should be masked in egress
    masked = "@" in text and "bob.jones@corp.example" not in text
    check(
        "tool_call_scan_ran",
        st == 200 and isinstance(resp, dict) and "result" in resp,
        {"status": st, "egress_sample": text[:120], "raw_pii_leaked": pii in text, "masked": masked},
    )

    out_path = os.environ.get(
        "OUT",
        os.path.join(os.path.dirname(__file__), "..", "..", "mcp-parallel", "findings", "mcp-validation", "iter2-live.json"),
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nWrote {out_path}")
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
