#!/usr/bin/env python3
"""Iteration 8 — policy plane matrix, sandbox crash recovery, flag/monitor scan matrix, SDK gate.

Writes: mcp-parallel/findings/mcp-validation/iter8-policy-crash-sdk.json
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

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "scripts/ralph/.mcp_scale_manifest.json"
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter8-policy-crash-sdk.json"
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
EMAIL = os.environ.get("AUDIT_EMAIL", "admin@zeroshield.io")
PASS = os.environ.get("AUDIT_PASS", "Adm1n!Pass#2024")
ORG = os.environ.get("ORG", "zeroshield")
SERVER = os.environ.get("SERVER", "everything-1")
SSN = "SSN 123-45-6789"
MARK = 8888


def _bust_scan_cache() -> None:
    if redis is None:
        return
    r = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
    for key in (f"mcp:scan_ver:{ORG}", f"mcp:scan_ver:{ORG}:{SERVER}"):
        try:
            r.incr(key)
        except redis.ResponseError:
            r.delete(key)
            r.incr(key)


def _login(client: httpx.Client) -> str:
    r = client.post(f"{CONTROL}/api/auth/token/", json={"email": EMAIL, "password": PASS}, timeout=60)
    r.raise_for_status()
    return r.json()["access"]


def _server_id(client: httpx.Client, tok: str) -> str:
    r = client.get(f"{CONTROL}/api/mcp-connector/servers/", headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    for row in rows:
        if row.get("server_slug") == SERVER:
            return row["id"]
    raise RuntimeError(f"server {SERVER} not found")


def _create_control(client: httpx.Client, tok: str, **fields) -> str:
    scope_type = fields.get("scope_type") or fields.get("scope", "org")
    if scope_type == "organization":
        scope_type = "org"
    body = {
        "scope_type": scope_type,
        "direction": fields["direction"],
        "tier": fields.get("tier", "tier1"),
        "enabled": True,
        "action": fields["action"],
        "priority": fields.get("priority", MARK),
    }
    if fields.get("server_id"):
        body["server"] = fields["server_id"]
    if fields.get("tool_name"):
        body["tool_name"] = fields["tool_name"]
    r = client.post(
        f"{CONTROL}/api/mcp-connector/scan-controls/",
        headers={"Authorization": f"Bearer {tok}"},
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    cid = r.json()["id"]
    _bust_scan_cache()
    return cid


def _delete_control(client: httpx.Client, tok: str, cid: str) -> None:
    client.delete(
        f"{CONTROL}/api/mcp-connector/scan-controls/{cid}/",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )
    _bust_scan_cache()


def _delete_all_marked(client: httpx.Client, tok: str) -> None:
    r = client.get(f"{CONTROL}/api/mcp-connector/scan-controls/", headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    for row in rows:
        if row.get("priority") in (MARK, MARK + 1):
            _delete_control(client, tok, row["id"])


def _egress_text(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    if body.get("result"):
        content = (body["result"] or {}).get("content") or []
        if content:
            return content[0].get("text", "")
    if body.get("error"):
        return json.dumps(body["error"])
    return ""


def _gw_echo(client: httpx.Client, key: str, message: str) -> dict:
    url = f"{GATEWAY}/gateway/{ORG}/mcp/{SERVER}"
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": message}},
    }
    r = client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=120)
    body = r.json()
    text = _egress_text(body)
    return {
        "status": r.status_code,
        "text": text,
        "blocked": "[BLOCKED]" in text,
        "masked": "123-45-6789" not in text and "SSN" in message,
        "raw_ssn_visible": "123-45-6789" in text,
        "body": body,
    }


def _ctl_echo(client: httpx.Client, tok: str, message: str = SSN) -> dict:
    r = client.post(
        f"{CONTROL}/api/mcp-connector/tools/call/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"name": "echo", "arguments": {"message": message}, "server_slug": SERVER},
        timeout=90,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    text = _egress_text(body) if not body.get("blocked") else (body.get("detail") or "")
    if isinstance(body.get("result"), dict):
        text = _egress_text({"result": body["result"]})
    return {
        "status": r.status_code,
        "decision": body.get("decision"),
        "text": text,
        "masked": "***" in text or "REDACTED" in text,
        "raw_ssn_visible": "123-45-6789" in text,
        "compliance_tags": body.get("compliance_tags") or [],
    }


def _enabled_tools(client: httpx.Client, tok: str) -> dict:
    r = client.get(
        f"{CONTROL}/api/mcp-connector/internal/enabled-tools/",
        headers={
            "Authorization": f"Bearer {tok}",
            "X-Org-Slug": ORG,
            "X-Server-Slug": SERVER,
            "X-Gateway-Internal-Key": os.environ.get("GATEWAY_INTERNAL_API_KEY", ""),
        },
        timeout=30,
    )
    if r.status_code != 200:
        r = client.get(
            f"{CONTROL}/api/mcp-connector/enabled-tools/?server_slug={SERVER}",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=30,
        )
    r.raise_for_status()
    return r.json()


def _sandbox_crash_recovery() -> dict:
    """Kill the org sandbox container; verify broker recreates and echo recovers."""
    # Broker names sandboxes mcp-sandbox-{sanitized-slug}; live stack uses zeroshield-mcp-sandbox.
    candidates = [f"{ORG}-mcp-sandbox", f"mcp-sandbox-{ORG.replace('_', '-')}"]
    cname = None
    for name in candidates:
        chk = subprocess.run(["docker", "inspect", name], capture_output=True, timeout=10)
        if chk.returncode == 0:
            cname = name
            break
    out: dict = {"container": cname, "candidates": candidates}
    if not cname:
        out["error"] = "sandbox container not found"
        out["recovered"] = False
        return out
    try:
        before = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", cname],
            capture_output=True,
            text=True,
            timeout=30,
        )
        out["running_before"] = before.stdout.strip() == "true"
        subprocess.run(["docker", "kill", cname], capture_output=True, timeout=30)
        time.sleep(2)
        recovered = False
        for _ in range(30):
            chk = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", cname],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if chk.returncode == 0 and chk.stdout.strip() == "true":
                recovered = True
                out["running_after_kill"] = True
                break
            time.sleep(2)
        out["recovered"] = recovered
    except Exception as exc:
        out["error"] = str(exc)
        out["recovered"] = False
    return out


def _run_sdk_gate() -> dict:
    gw = ROOT / "gateway"
    py = gw / ".venv/bin/python"
    if not py.exists():
        return {"ok": False, "reason": "no gateway venv"}
    proc = subprocess.run(
        [str(py), "-m", "pytest", "ai_mesh_gateway/tests/test_openai_sdk_compat.py", "-q", "--tb=no"],
        cwd=str(gw),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {"ok": proc.returncode == 0, "stdout_tail": proc.stdout[-500:], "exit": proc.returncode}


def main() -> int:
    report: dict = {"matrix": [], "policy_plane": {}, "sandbox_crash": {}, "sdk": {}, "findings": {}}
    mf = json.loads(MANIFEST.read_text())
    key = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)
    created: list[str] = []

    with httpx.Client() as client:
        tok = _login(client)
        server_id = _server_id(client, tok)

        def run_case(name: str, expect: dict, setup=None) -> None:
            if setup:
                setup()
            time.sleep(1.5)
            gw = _gw_echo(client, key, SSN)
            ctl = _ctl_echo(client, tok, SSN)
            row = {
                "case": name,
                "gateway": gw,
                "control": ctl,
                "pass": True,
            }
            for k, v in expect.items():
                if gw.get(k) != v:
                    row["pass"] = False
                    row.setdefault("failures", []).append(f"gateway.{k} expected {v} got {gw.get(k)}")
            report["matrix"].append(row)

        # Policy plane @ 0 scan controls (F-005)
        et = _enabled_tools(client, tok)
        report["policy_plane"]["zero_controls"] = {
            "scan_controls_configured": et.get("scan_controls_configured"),
            "gateway": _gw_echo(client, key, SSN),
            "control": _ctl_echo(client, tok, SSN),
        }
        report["policy_plane"]["pass"] = (
            et.get("scan_controls_configured") is False
            and report["policy_plane"]["zero_controls"]["gateway"].get("raw_ssn_visible") is True
            and report["policy_plane"]["zero_controls"]["control"].get("masked") is True
        )

        # Flag / monitor matrix
        cid = _create_control(client, tok, scope_type="org", direction="both", action="monitor", tier="tier1")
        created.append(cid)
        run_case("org_both_monitor", {"blocked": False, "raw_ssn_visible": True})
        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1)

        # "Flag" in the UI maps to monitor (detect + tag, allow) — no separate flag action.
        cid = _create_control(client, tok, scope_type="org", direction="input", action="monitor", tier="tier1")
        created.append(cid)
        run_case("org_input_monitor_tag", {"blocked": False})
        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1)

        # F-009 fixed: output-only block should hard-block maskable PII on egress
        cid = _create_control(client, tok, scope_type="org", direction="output", action="block", tier="tier1")
        created.append(cid)
        et2 = _enabled_tools(client, tok)
        eff = et2.get("effective_scan_controls") or {}
        report["findings"]["F-009"] = {
            "tier1_input_enabled": (eff.get("tier1_input") or {}).get("enabled"),
            "tier1_output_action": (eff.get("tier1_output") or {}).get("action"),
        }
        run_case("org_output_block_maskable_pii", {"blocked": True})
        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1)

        _delete_all_marked(client, tok)

    report["sandbox_crash"] = _sandbox_crash_recovery()
    mf2 = json.loads(MANIFEST.read_text())
    key2 = next(o["gateway_key"] for o in mf2["orgs"] if o["slug"] == ORG)
    with httpx.Client() as client:
        post = _gw_echo(client, key2, f"POST-CRASH {SSN}")
    report["sandbox_crash"]["post_recovery_echo"] = post
    report["sandbox_crash"]["pass"] = (
        report["sandbox_crash"].get("post_recovery_echo", {}).get("status") == 200
    )

    report["sdk"] = _run_sdk_gate()

    report["checks"] = {
        "matrix_pass": all(r["pass"] for r in report["matrix"]),
        "policy_plane_pass": report["policy_plane"].get("pass"),
        "sandbox_crash_pass": report["sandbox_crash"].get("pass"),
        "sdk_pass": report["sdk"].get("ok"),
    }
    report["ok"] = all(v for v in report["checks"].values() if v is not None)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps({"ok": report["ok"], "checks": report["checks"]}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
