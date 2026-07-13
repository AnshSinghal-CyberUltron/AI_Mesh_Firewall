#!/usr/bin/env python3
"""Iteration 5 — multi-org isolation + compliance tags when scanning off + scan UI.

Writes: mcp-parallel/findings/mcp-validation/iter5-multiorg-compliance.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "scripts/ralph/.mcp_scale_manifest.json"
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter5-multiorg-compliance.json"
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
PASS = os.environ.get("AUDIT_PASS", "Adm1n!Pass#2024")
SERVER = os.environ.get("SERVER", "everything-1")
SSN = "SSN 123-45-6789"


def _internal_key() -> str:
    return subprocess.check_output(
        ["docker", "exec", "ai_mesh_firewall-gateway-1", "printenv", "GATEWAY_INTERNAL_API_KEY"],
        text=True,
    ).strip()


def _login(client: httpx.Client, email: str) -> str:
    r = client.post(f"{CONTROL}/api/auth/token/", json={"email": email, "password": PASS}, timeout=30)
    r.raise_for_status()
    return r.json()["access"]


def _gw_echo(client: httpx.Client, org: str, key: str, message: str) -> dict:
    rid = f"iter5-{uuid.uuid4().hex[:8]}"
    payload = {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": message}},
    }
    r = client.post(
        f"{GATEWAY}/gateway/{org}/mcp/{SERVER}",
        json=payload,
        headers={"Authorization": f"Bearer {key}"},
        timeout=90,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    text = ""
    if isinstance(body, dict) and body.get("result"):
        content = (body["result"] or {}).get("content") or []
        if content:
            text = content[0].get("text", "")
    return {"request_id": rid, "status": r.status_code, "egress_text": text, "body": body}


def _latest_event(client: httpx.Client, tok: str, tool: str = "echo") -> dict | None:
    r = client.get(
        f"{CONTROL}/api/mcp-connector/events/",
        headers={"Authorization": f"Bearer {tok}"},
        params={"tool": tool, "limit": 5, "hours": 1},
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def _cross_tenant_matrix(client: httpx.Client, orgs: list[dict]) -> list[dict]:
    results = []
    for attacker in orgs:
        for victim in orgs:
            if attacker["slug"] == victim["slug"]:
                continue
            r = client.post(
                f"{GATEWAY}/gateway/{victim['slug']}/mcp/{SERVER}",
                json={
                    "jsonrpc": "2.0",
                    "id": "x-tenant",
                    "method": "tools/call",
                    "params": {"name": "echo", "arguments": {"message": "cross"}},
                },
                headers={"Authorization": f"Bearer {attacker['gateway_key']}"},
                timeout=30,
            )
            results.append({
                "attacker": attacker["slug"],
                "victim": victim["slug"],
                "status": r.status_code,
                "pass": r.status_code in (401, 403),
            })
    return results


def _run_multi_org_harness() -> dict:
    env = {**os.environ, "ROUNDS": "1", "HARNESS_WORKERS": "16"}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts/mcp_multi_org_harness.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    tail = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-1000:]
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "tail": tail.strip()[-500:],
    }


def main() -> int:
    mf = json.loads(MANIFEST.read_text())
    orgs = mf["orgs"]
    ih_base = {"X-Gateway-Internal-Key": _internal_key(), "X-Gateway-Auth": "true"}
    report: dict = {"orgs": [], "cross_tenant": [], "harness": {}, "checks": {}, "ok": False}

    with httpx.Client() as client:
        for org in orgs:
            slug = org["slug"]
            tok = _login(client, org["email"])
            ih = {**ih_base, "X-Org-Slug": slug}

            sc = client.get(
                f"{CONTROL}/api/mcp-connector/scan-controls/",
                headers={"Authorization": f"Bearer {tok}"},
                timeout=30,
            )
            n_rows = len(sc.json()) if isinstance(sc.json(), list) else 0

            en = client.get(
                f"{CONTROL}/api/mcp-connector/internal/enabled-tools/?server_slug={SERVER}",
                headers=ih,
                timeout=30,
            )
            en.raise_for_status()
            enabled = en.json()

            marker = f"ITER5-{slug}-{uuid.uuid4().hex[:6]}"
            echo = _gw_echo(client, slug, org["gateway_key"], f"{marker} {SSN}")
            time.sleep(1.5)
            ev = _latest_event(client, tok)
            tags = (ev or {}).get("compliance_tags") or []
            meta = (ev or {}).get("metadata") or {}
            findings = (ev or {}).get("scan_findings") or []

            row = {
                "slug": slug,
                "scan_control_rows": n_rows,
                "scan_controls_configured": enabled.get("scan_controls_configured"),
                "mcp_tier2_enabled": enabled.get("mcp_tier2_enabled"),
                "decision": (ev or {}).get("decision"),
                "compliance_tags": tags,
                "scan_findings_count": len(findings) if isinstance(findings, list) else 0,
                "raw_ssn_visible": "123-45-6789" in echo.get("egress_text", ""),
            }
            if n_rows == 0:
                row["pass"] = (
                    enabled.get("scan_controls_configured") is False
                    and (ev or {}).get("decision") == "scan_skipped"
                    and len(tags) == 0
                    and row["raw_ssn_visible"] is True
                )
            else:
                row["pass"] = (
                    enabled.get("scan_controls_configured") is True
                    and row["raw_ssn_visible"] is False
                )
            report["orgs"].append(row)

        report["cross_tenant"] = _cross_tenant_matrix(client, orgs)

    report["harness"] = _run_multi_org_harness()
    report["checks"]["all_orgs_pass"] = all(o["pass"] for o in report["orgs"])
    report["checks"]["cross_tenant_pass"] = all(c["pass"] for c in report["cross_tenant"])
    report["checks"]["harness_pass"] = report["harness"].get("ok")
    report["ok"] = all(report["checks"].values())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
