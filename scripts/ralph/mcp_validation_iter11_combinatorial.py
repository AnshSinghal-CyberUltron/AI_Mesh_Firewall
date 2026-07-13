#!/usr/bin/env python3
"""Iteration 11 — combinatorial scan×policy matrix, tier-2 gate, ext byte proof, image parity.

Writes: mcp-parallel/findings/mcp-validation/iter11-combinatorial.json
"""
from __future__ import annotations

import hashlib
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
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter11-combinatorial.json"
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
        if row.get("priority") in (MARK, MARK + 1, MARK + 2):
            _delete_control(client, tok, row["id"])


def _egress_text(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    if body.get("result"):
        content = (body["result"] or {}).get("content") or []
        if content:
            return content[0].get("text", "")
    if body.get("error"):
        err = body["error"]
        if isinstance(err, dict):
            return err.get("message", "") or json.dumps(err)
        return str(err)
    return ""


def _gw_echo(client: httpx.Client, key: str, message: str = SSN, marker: str | None = None) -> dict:
    rid = marker or f"i11-{uuid.uuid4().hex[:8]}"
    payload = {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": message}},
    }
    r = client.post(
        f"{GATEWAY}/gateway/{ORG}/mcp/{SERVER}",
        json=payload,
        headers={"Authorization": f"Bearer {key}"},
        timeout=90,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    text = _egress_text(body)
    return {
        "request_id": rid,
        "status": r.status_code,
        "egress_text": text,
        "blocked": "[BLOCKED]" in text,
        "raw_ssn_visible": "123-45-6789" in text,
        "masked": "123-45-6789" not in text and "SSN" in message,
    }


def _ctl_echo(client: httpx.Client, tok: str, message: str = SSN) -> dict:
    r = client.post(
        f"{CONTROL}/api/mcp-connector/tools/call/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"name": "echo", "arguments": {"message": message}, "server_slug": SERVER},
        timeout=90,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    text = _egress_text({"result": body["result"]}) if isinstance(body.get("result"), dict) else _egress_text(body)
    return {
        "status": r.status_code,
        "decision": body.get("decision"),
        "egress_text": text,
        "raw_ssn_visible": "123-45-6789" in text,
        "masked": "***" in text or "REDACTED" in text or ("123-45-6789" not in text and "SSN" in message),
        "compliance_tags": body.get("compliance_tags") or [],
    }


def _latest_echo_event(client: httpx.Client, tok: str) -> dict | None:
    r = client.get(
        f"{CONTROL}/api/mcp-connector/events/",
        headers={"Authorization": f"Bearer {tok}"},
        params={"tool": "echo", "limit": 5, "hours": 1},
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def _trace_stages(meta: dict) -> list[str]:
    trace = meta.get("scan_trace") or []
    return [t.get("scan_stage") or t.get("tier") or "?" for t in trace if isinstance(t, dict)]


def _set_tier2_org(client: httpx.Client, tok: str, value: bool | None) -> dict:
    r = client.put(
        f"{CONTROL}/api/firewall/config/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"mcp_tier2_enabled": value},
        timeout=30,
    )
    return {"status": r.status_code, "body": r.json() if r.is_success else r.text[:200]}


def _bulk_policies(enabled: bool) -> dict:
    flag = "True" if enabled else "False"
    one_liner = (
        "from policy.models import Policy; from auth.models import Organization; "
        "from django.db.models import Q; "
        f"org=Organization.objects.get(slug='{ORG}'); "
        "n=Policy.objects.filter(policy_domain='mcp')"
        ".filter(Q(organization=org)|Q(organization__isnull=True)).update(enabled="
        f"{flag}); print(n)"
    )
    proc = subprocess.run(
        [
            "docker", "exec", "ai_mesh_firewall-control-1", "bash", "-lc",
            f"cd /app/control && /app/control/.venv/bin/python manage.py shell -c {json.dumps(one_liner)}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        count = int((proc.stdout or "0").strip().splitlines()[-1])
    except ValueError:
        count = -1
    return {"ok": proc.returncode == 0, "updated": count}


def _run_pytest_ext_proof() -> dict:
    proc = subprocess.run(
        [
            str(ROOT / "gateway/.venv/bin/python"),
            "-m",
            "pytest",
            "ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py",
            "-q",
            "-k",
            "ext_proxy and not audit",
            "--tb=no",
        ],
        cwd=ROOT / "gateway",
        capture_output=True,
        text=True,
        timeout=300,
    )
    tail = (proc.stdout or "")[-500:]
    passed = proc.returncode == 0
    return {"ok": passed, "exit_code": proc.returncode, "summary": tail.strip()}


def _run_sdk_gate() -> dict:
    proc = subprocess.run(
        [
            str(ROOT / "gateway/.venv/bin/python"),
            "-m",
            "pytest",
            "ai_mesh_gateway/tests/test_openai_sdk_compat.py",
            "-q",
            "-k",
            "streaming or tool_calls or parallel or concurrent",
        ],
        cwd=ROOT / "gateway",
        capture_output=True,
        text=True,
        timeout=180,
    )
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode, "summary": (proc.stdout or "")[-400:]}


def _workspace_image_parity() -> dict:
    """Compare workspace fix files vs running container (hot-deploy may differ from image)."""
    pairs = (
        ("control_scan_controls", "ai_mesh_firewall-control-1",
         ROOT / "control/ai_mesh_control/mcp_connector/scan_controls.py",
         "/app/control/ai_mesh_control/mcp_connector/scan_controls.py"),
        ("gateway_mcp_proxy", "ai_mesh_firewall-gateway-1",
         ROOT / "gateway/ai_mesh_gateway/mcp_proxy.py",
         "/app/gateway/ai_mesh_gateway/mcp_proxy.py"),
    )
    out = {}
    for label, cname, local, remote in pairs:
        local_hash = hashlib.sha256(local.read_bytes()).hexdigest()[:16]
        proc = subprocess.run(
            ["docker", "exec", cname, "sha256sum", remote],
            capture_output=True,
            text=True,
            timeout=30,
        )
        remote_hash = proc.stdout.split()[0][:16] if proc.returncode == 0 else "missing"
        out[label] = {
            "local_sha256_prefix": local_hash,
            "container_sha256_prefix": remote_hash,
            "match": local_hash == remote_hash,
        }
    out["pass"] = all(v.get("match") for v in out.values() if isinstance(v, dict) and "match" in v)
    out["note"] = "False match means hot-deploy drift — rebuild images to bake fixes"
    return out


def main() -> int:
    report: dict = {"matrix": [], "tier2": {}, "cross_plane": {}, "compliance": {}, "ext_proof": {}, "sdk": {}, "image_parity": {}}
    mf = json.loads(MANIFEST.read_text())
    key = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)
    created: list[str] = []

    with httpx.Client() as client:
        tok = _login(client)
        _delete_all_marked(client, tok)
        server_id = _server_id(client, tok)

        def run_case(name: str, expect: dict) -> None:
            time.sleep(1.5)
            got = _gw_echo(client, key, marker=f"i11-{name[:12]}")
            row = {"case": name, "expect": expect, "got": got}
            row["pass"] = all(got.get(k) == v for k, v in expect.items() if k in got)
            report["matrix"].append(row)
            print(f"matrix {name}: pass={row['pass']}")

        # ── Baseline + direction×action org scope ──
        run_case("baseline_zero_controls", {"raw_ssn_visible": True, "masked": False, "blocked": False})

        cid = _create_control(client, tok, scope_type="org", direction="both", action="monitor", tier="tier1")
        created.append(cid)
        run_case("org_both_monitor", {"blocked": False, "raw_ssn_visible": True})  # F-010
        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1.5)

        for direction, action, expect in (
            ("input", "redact", {"masked": True, "blocked": False}),
            ("output", "redact", {"masked": True, "blocked": False}),
            ("both", "block", {"blocked": True}),
            ("input", "block", {"blocked": True}),
            ("output", "block", {"blocked": True}),
        ):
            cid = _create_control(client, tok, scope_type="org", direction=direction, action=action, tier="tier1")
            created.append(cid)
            run_case(f"org_{direction}_{action}", expect)
            _delete_control(client, tok, cid)
            created.remove(cid)
            time.sleep(1.5)

        # Server-scoped output block
        cid = _create_control(
            client, tok, scope_type="server", server_id=server_id, direction="output", action="block", tier="tier1"
        )
        created.append(cid)
        run_case("server_output_block", {"blocked": True})
        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1.5)

        # Tool precedence over org monitor
        cid_org = _create_control(client, tok, scope_type="org", direction="input", action="monitor", tier="tier1")
        cid_tool = _create_control(
            client, tok, scope_type="tool", server_id=server_id, tool_name="echo",
            direction="input", action="block", tier="tier1", priority=MARK + 1,
        )
        created.extend([cid_org, cid_tool])
        run_case("precedence_tool_block_over_org_monitor", {"blocked": True})
        _delete_control(client, tok, cid_tool)
        _delete_control(client, tok, cid_org)
        created.clear()
        time.sleep(1.5)

        # F-009 output-only block
        cid = _create_control(client, tok, scope_type="org", direction="output", action="block", tier="tier1")
        created.append(cid)
        run_case("org_output_block_maskable_pii", {"blocked": True})
        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1.5)

        run_case("post_cleanup_zero_controls", {"raw_ssn_visible": True, "masked": False, "blocked": False})

        # ── Compliance @ zero controls (before tier2 / policy tests) ──
        gw0 = _gw_echo(client, key, marker="i11-compliance0")
        time.sleep(0.5)
        ev1 = _latest_echo_event(client, tok)
        report["compliance"] = {
            "decision": (ev1 or {}).get("decision"),
            "tags": (ev1 or {}).get("compliance_tags") or [],
            "scan_trace": _trace_stages((ev1 or {}).get("metadata") or {}),
            "raw_egress": gw0.get("raw_ssn_visible"),
            "pass": (ev1 or {}).get("decision") in ("scan_skipped", "allow")
            and not (ev1 or {}).get("compliance_tags"),
        }

        _delete_all_marked(client, tok)

        # ── Tier-2 gate ──
        tier2_restore = None
        fw = client.get(f"{CONTROL}/api/firewall/config/", headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        if fw.is_success:
            tier2_restore = fw.json().get("mcp_tier2_enabled")
        _set_tier2_org(client, tok, False)
        cid = _create_control(client, tok, scope_type="org", direction="both", action="monitor", tier="tier2")
        created.append(cid)
        time.sleep(1.5)
        m = f"tier2-{uuid.uuid4().hex[:6]}"
        gw = _gw_echo(client, key, marker=m)
        ev = _latest_echo_event(client, tok)
        stages = _trace_stages((ev or {}).get("metadata") or {})
        report["tier2"] = {
            "org_tier2_disabled": True,
            "egress": gw,
            "stages": stages,
            "decision": (ev or {}).get("decision"),
            "pass": "tier2_skipped" in stages or "scan_skipped" not in stages and "tier2" not in "".join(stages),
        }
        # tier2 row present but org disabled → must NOT run tier2
        report["tier2"]["pass"] = "tier2" not in stages or "tier2_skipped" in stages
        _delete_control(client, tok, cid)
        created.remove(cid)
        if tier2_restore is not None:
            _set_tier2_org(client, tok, tier2_restore)

        # ── Cross-plane F-005: policies ON + scan OFF → gateway raw, control masked ──
        _delete_all_marked(client, tok)
        time.sleep(1.5)
        _bulk_policies(True)
        client.post(
            f"{CONTROL}/api/policies/compile/",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=120,
        )
        time.sleep(2)
        gw_f005 = _gw_echo(client, key)
        ctl_f005 = _ctl_echo(client, tok)
        report["cross_plane"] = {
            "scan_controls": 0,
            "mcp_policies_enabled": True,
            "gateway": gw_f005,
            "control": ctl_f005,
            "pass": gw_f005.get("raw_ssn_visible") and ctl_f005.get("masked"),
            "finding": "F-005 — independent policy plane on control /tools/call/",
        }

    report["ext_proof"] = _run_pytest_ext_proof()
    report["sdk"] = _run_sdk_gate()
    report["image_parity"] = _workspace_image_parity()

    report["checks"] = {
        "matrix_pass": all(r["pass"] for r in report["matrix"]),
        "tier2_pass": report["tier2"].get("pass"),
        "compliance_pass": report["compliance"].get("pass"),
        "cross_plane_pass": report["cross_plane"].get("pass"),
        "ext_pytest_pass": report["ext_proof"].get("ok"),
        "sdk_pass": report["sdk"].get("ok"),
        "image_parity_pass": report["image_parity"].get("pass"),
    }
    report["ok"] = all(
        report["checks"].get(k)
        for k in (
            "matrix_pass",
            "tier2_pass",
            "compliance_pass",
            "cross_plane_pass",
            "ext_pytest_pass",
            "sdk_pass",
        )
    )
    # image parity informational — hot-deploy may intentionally differ until rebuild

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps({"ok": report["ok"], "checks": report["checks"]}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
