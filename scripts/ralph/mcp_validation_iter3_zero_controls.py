#!/usr/bin/env python3
"""Iteration 3 — live proof that 0 scan controls disables two-tier scan on gateway path.

Uses internal enabled-tools + gateway echo; does NOT mutate DB (matrix harness handles that).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "scripts/ralph/.mcp_scale_manifest.json"
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter3-zero-controls.json"
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
ORG = os.environ.get("ORG_SLUG", "zeroshield")
SERVER = os.environ.get("MCP_SERVER", "everything-1")
SSN = "SSN 123-45-6789"


def _json(method: str, url: str, headers: dict | None = None, body: dict | None = None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = resp.read().decode()
        return resp.status, json.loads(raw) if raw else {}


def _internal_key() -> str:
    return subprocess.check_output(
        ["docker", "exec", "ai_mesh_firewall-gateway-1", "printenv", "GATEWAY_INTERNAL_API_KEY"],
        text=True,
    ).strip()


def main() -> int:
    mf = json.loads(MANIFEST.read_text())
    gkey = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)
    key = _internal_key()
    ih = {"X-Gateway-Internal-Key": key, "X-Gateway-Auth": "true", "X-Org-Slug": ORG}

    st, tok = _json("POST", f"{CONTROL}/api/auth/token/", body={"email": "admin@zeroshield.io", "password": "Adm1n!Pass#2024"})
    access = tok["access"]
    st, rows = _json("GET", f"{CONTROL}/api/mcp-connector/scan-controls/", headers={"Authorization": f"Bearer {access}"})
    n = len(rows) if isinstance(rows, list) else 0

    st, en = _json("GET", f"{CONTROL}/api/mcp-connector/internal/enabled-tools/?server_slug={SERVER}", headers=ih)
    configured = en.get("scan_controls_configured")

    rpc = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": SSN}},
    }
    st, resp = _json(
        "POST",
        f"{GATEWAY}/gateway/{ORG}/mcp/{SERVER}",
        headers={"Authorization": f"Bearer {gkey}"},
        body=rpc,
    )
    text = ""
    if isinstance(resp, dict) and resp.get("result"):
        content = (resp["result"] or {}).get("content") or []
        if content:
            text = content[0].get("text", "")

    result = {
        "ok": True,
        "scan_control_rows": n,
        "scan_controls_configured": configured,
        "egress_text": text,
        "raw_ssn_visible": "123-45-6789" in text,
        "masked": "***" in text or "REDACTED" in text,
        "expect_when_zero": {"configured": False, "raw_ssn_visible": True},
        "expect_when_configured": {"configured": True, "masked": True},
    }
    if n == 0:
        result["ok"] = configured is False and result["raw_ssn_visible"]
    else:
        result["ok"] = configured is True and result["masked"] and not result["raw_ssn_visible"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
