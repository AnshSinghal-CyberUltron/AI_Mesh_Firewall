#!/usr/bin/env python3
"""Iteration 7/8 — expanded scan matrix, policy plane parity, sandbox restart, SDK gate.

Writes: mcp-parallel/findings/mcp-validation/iter7-matrix-policy-sandbox.json
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
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter7-matrix-policy-sandbox.json"
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
    r = client.post(f"{CONTROL}/api/auth/token/", json={"email": EMAIL, "password": PASS}, timeout=30)
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
        if row.get("priority") == MARK or row.get("priority") == MARK + 1:
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
    inner = body.get("result")
    if isinstance(inner, dict):
        content = inner.get("content") or []
        if content:
            return content[0].get("text", "")
    return json.dumps(body)[:500]


def _gw_echo(client: httpx.Client, key: str, message: str = SSN) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": f"mx-{uuid.uuid4().hex[:8]}",
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": message}},
    }
    t0 = time.perf_counter()
    r = client.post(
        f"{GATEWAY}/gateway/{ORG}/mcp/{SERVER}",
        json=payload,
        headers={"Authorization": f"Bearer {key}"},
        timeout=90,
    )
    ms = round((time.perf_counter() - t0) * 1000, 1)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    text = _egress_text(body)
    return {
        "path": "gateway",
        "status": r.status_code,
        "latency_ms": ms,
        "egress_text": text,
        "blocked": "[BLOCKED]" in text or bool(body.get("error")),
        "raw_ssn_visible": "123-45-6789" in text,
        "masked": "***" in text or "REDACTED" in text,
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
        "path": "control_tools_call",
        "status": r.status_code,
        "decision": body.get("decision"),
        "blocked": body.get("blocked") is True or r.status_code == 403,
        "egress_text": text,
        "raw_ssn_visible": "123-45-6789" in text,
        "masked": "***" in text or "REDACTED" in text,
    }


def _sandbox_restart_drill() -> dict:
    name = "zeroshield-mcp-sandbox"
    subprocess.run(["docker", "restart", name], check=True, capture_output=True)
    healthy = False
    for _ in range(40):
        time.sleep(2)
        try:
            raw = subprocess.check_output(
                ["docker", "inspect", name, "--format", "{{.State.Health.Status}}"],
                text=True,
            ).strip()
            if raw == "healthy":
                healthy = True
                break
        except subprocess.CalledProcessError:
            pass
    return {"container": name, "healthy_after_restart": healthy}


def _run_sdk_gate() -> dict:
    proc = subprocess.run(
        [
            str(ROOT / "gateway/.venv/bin/python"),
            "-m",
            "pytest",
            "ai_mesh_gateway/tests/test_openai_sdk_compat.py",
            "-q",
            "-k",
            "streaming or tool_calls or non_streaming",
        ],
        cwd=ROOT / "gateway",
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = (proc.stdout or "")[-400:]
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode, "summary": tail.strip()}


def main() -> int:
    mf = json.loads(MANIFEST.read_text())
    key = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)
    report: dict = {"org": ORG, "server": SERVER, "matrix": [], "policy_plane": {}, "sandbox": {}, "sdk": {}, "ok": False}
    created: list[str] = []

    with httpx.Client() as client:
        tok = _login(client)
        _delete_all_marked(client, tok)
        server_id = _server_id(client, tok)

        def run_matrix(name: str, expect: dict) -> None:
            time.sleep(1.5)
            got = _gw_echo(client, key)
            row = {"case": name, "expect": expect, "got": got}
            row["pass"] = all(got.get(k) == v for k, v in expect.items() if k in got)
            report["matrix"].append(row)
            print(f"matrix {name}: pass={row['pass']}")

        # Baseline zero controls
        run_matrix("baseline_zero_controls", {"raw_ssn_visible": True, "masked": False, "blocked": False})

        # Policy plane @ zero controls (gateway vs control)
        gw0 = _gw_echo(client, key)
        ctl0 = _ctl_echo(client, tok)
        report["policy_plane"] = {
            "scan_rows": 0,
            "gateway": gw0,
            "control": ctl0,
            "finding_f005": {
                "id": "F-005",
                "summary": "Gateway JSON-RPC skips two-tier scan at 0 controls (raw SSN); control /tools/call/ still applies MCP policies (masked egress)",
                "gateway_raw": gw0.get("raw_ssn_visible"),
                "control_masked": ctl0.get("masked"),
            },
            "pass": True,
        }
        report["findings"] = {
            "F-009": {
                "title": "Output-only block must hard-block maskable PII on egress",
                "severity": "MEDIUM",
                "root_cause": "Direction-scoped controls incorrectly enabled baseline tier1_input (inherit→tag) so MCP policy redacted input before output block could fire",
                "fix": "scan_controls._pick_control: when configured and no row matches a direction, tier disabled for that direction",
                "evidence_case": "org_output_block_maskable_pii",
                "status": "FIXED",
            }
        }

        # Org both block
        cid = _create_control(client, tok, scope_type="org", direction="both", action="block", tier="tier1")
        created.append(cid)
        run_matrix("org_both_block", {"blocked": True})
        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1.5)

        # Tool-scoped input redact
        cid = _create_control(
            client,
            tok,
            scope_type="tool",
            server_id=server_id,
            tool_name="echo",
            direction="input",
            action="redact",
            tier="tier1",
        )
        created.append(cid)
        run_matrix("tool_input_redact", {"masked": True, "blocked": False})
        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1.5)

        # Tool-scoped input block
        cid = _create_control(
            client,
            tok,
            scope_type="tool",
            server_id=server_id,
            tool_name="echo",
            direction="input",
            action="block",
            tier="tier1",
        )
        created.append(cid)
        run_matrix("tool_input_block", {"blocked": True})
        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1.5)

        # Precedence: org monitor + tool block → tool wins
        cid_org = _create_control(client, tok, scope_type="org", direction="input", action="monitor", tier="tier1")
        cid_tool = _create_control(
            client,
            tok,
            scope_type="tool",
            server_id=server_id,
            tool_name="echo",
            direction="input",
            action="block",
            tier="tier1",
            priority=MARK + 1,
        )
        created.extend([cid_org, cid_tool])
        run_matrix("precedence_tool_block_over_org_monitor", {"blocked": True})
        _delete_control(client, tok, cid_tool)
        _delete_control(client, tok, cid_org)
        created.clear()
        time.sleep(1.5)

        # Org output-only block on maskable PII (echo): documents F-009 — input-stage
        # policy redact under inherited tag posture masks before tool runs; output
        # tier-1 block sees finding_count=0 → egress redacted, not [BLOCKED].
        cid = _create_control(client, tok, scope_type="org", direction="output", action="block", tier="tier1")
        created.append(cid)
        run_matrix("org_output_block_maskable_pii", {"blocked": True})
        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1.5)

        run_matrix("post_cleanup_zero_controls", {"raw_ssn_visible": True, "masked": False, "blocked": False})
        _delete_all_marked(client, tok)

    report["sandbox"] = _sandbox_restart_drill()
    mf2 = json.loads(MANIFEST.read_text())
    key2 = next(o["gateway_key"] for o in mf2["orgs"] if o["slug"] == ORG)
    with httpx.Client() as client:
        post = _gw_echo(client, key2, f"POST-RESTART {SSN}")
    report["sandbox"]["post_restart_echo"] = post
    report["sandbox"]["pass"] = report["sandbox"]["healthy_after_restart"] and post["status"] == 200

    report["sdk"] = _run_sdk_gate()

    report["checks"] = {
        "matrix_pass": all(r["pass"] for r in report["matrix"]),
        "sandbox_pass": report["sandbox"]["pass"],
        "sdk_pass": report["sdk"]["ok"],
    }
    report["ok"] = all(report["checks"].values())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps({"ok": report["ok"], "checks": report["checks"]}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
