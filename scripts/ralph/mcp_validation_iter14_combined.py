#!/usr/bin/env python3
"""Iteration 14+ — combined multi-org validation: F-013, scan isolation, F-005, regressions.

Writes: mcp-parallel/findings/mcp-validation/iter14-combined.json
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
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter14-combined.json"
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
SERVER = os.environ.get("SERVER", "everything-1")
SSN = "123-45-6789"
SSN_TEXT = f"patient SSN is {SSN}"
MARK = 8888
VAL_PREFIX = "VAL_ITER14_"


def _bust_scan_cache(org: str) -> None:
    if redis is None:
        return
    r = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
    for key in (f"mcp:scan_ver:{org}", f"mcp:scan_ver:{org}:{SERVER}"):
        try:
            r.incr(key)
        except redis.ResponseError:
            r.delete(key)
            r.incr(key)


def _login(client: httpx.Client, email: str, password: str | None = None) -> str:
    pw = password or os.environ.get("AUDIT_PASS", "Adm1n!Pass#2024")
    for attempt in range(6):
        r = client.post(f"{CONTROL}/api/auth/token/", json={"email": email, "password": pw}, timeout=60)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "5"))
            time.sleep(min(wait, 30))
            continue
        r.raise_for_status()
        return r.json()["access"]
    r.raise_for_status()
    return ""


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


def _gw_echo(client: httpx.Client, org: str, key: str, message: str = SSN_TEXT) -> dict:
    r = client.post(
        f"{GATEWAY}/gateway/{org}/mcp/{SERVER}",
        json={
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": message}},
        },
        headers={"Authorization": f"Bearer {key}"},
        timeout=90,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    text = _egress_text(body)
    return {
        "status": r.status_code,
        "egress_text": text,
        "blocked": "[BLOCKED]" in text,
        "raw_ssn_visible": SSN in text,
    }


def _ctl_echo(client: httpx.Client, tok: str, message: str = SSN_TEXT) -> dict:
    r = client.post(
        f"{CONTROL}/api/mcp-connector/tools/call/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"name": "echo", "arguments": {"message": message}, "server_slug": SERVER},
        timeout=90,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    text = ""
    if isinstance(body.get("result"), dict):
        text = _egress_text({"result": body["result"]})
    else:
        text = _egress_text(body)
    return {
        "status": r.status_code,
        "decision": body.get("decision"),
        "egress_text": text,
        "raw_ssn_visible": SSN in text,
        "masked": SSN not in text and "SSN" in message,
    }


def _latest_event(client: httpx.Client, tok: str) -> dict | None:
    r = client.get(
        f"{CONTROL}/api/mcp-connector/events/",
        headers={"Authorization": f"Bearer {tok}"},
        params={"tool": "echo", "limit": 3, "hours": 1},
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def _django_shell(script: str) -> dict:
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "ai_mesh_firewall-control-1",
            "bash",
            "-lc",
            f"cd /app/control && /app/control/.venv/bin/python manage.py shell -c {json.dumps(script)}",
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    return {
        "ok": proc.returncode == 0,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "")[-400:],
    }


def _create_scan_block(client: httpx.Client, tok: str, org: str) -> str:
    body = {
        "scope_type": "org",
        "direction": "both",
        "tier": "tier1",
        "enabled": True,
        "action": "block",
        "priority": MARK,
    }
    r = client.post(
        f"{CONTROL}/api/mcp-connector/scan-controls/",
        headers={"Authorization": f"Bearer {tok}"},
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    cid = r.json()["id"]
    _bust_scan_cache(org)
    return cid


def _delete_scan_control(client: httpx.Client, tok: str, cid: str, org: str) -> None:
    client.delete(
        f"{CONTROL}/api/mcp-connector/scan-controls/{cid}/",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )
    _bust_scan_cache(org)


def _delete_all_scan_controls(client: httpx.Client, tok: str, org: str) -> int:
    """Remove every scan-control row for this org (restore off-by-default baseline)."""
    r = client.get(
        f"{CONTROL}/api/mcp-connector/scan-controls/",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    for row in rows:
        client.delete(
            f"{CONTROL}/api/mcp-connector/scan-controls/{row['id']}/",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=30,
        )
    _bust_scan_cache(org)
    return len(rows)


def _delete_marked_controls(client: httpx.Client, tok: str, org: str) -> None:
    r = client.get(
        f"{CONTROL}/api/mcp-connector/scan-controls/",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    for row in rows:
        if row.get("priority") == MARK:
            _delete_scan_control(client, tok, row["id"], org)


def _create_f013_policy(org_slug: str) -> dict:
    code = f"{VAL_PREFIX}F013"
    script = (
        "from policy.models import Policy, Rule; from auth.models import Organization; "
        f"org=Organization.objects.get(slug='{org_slug}'); code='{code}'; "
        "Policy.objects.filter(code=code).delete(); "
        "p=Policy.objects.create(code=code, name=code, policy_domain='mcp', "
        "organization=org, category='pii', severity='HIGH', enabled=True, priority=9999); "
        "Rule.objects.create(policy=p, name=code+'-rule', rule_type='regex', "
        "condition={'preset':'us_ssn','direction':'input','scope':'entire'}, "
        "action='block', redaction_config={}, priority=10, enabled=True); "
        "print(p.id)"
    )
    out = _django_shell(script)
    pid = None
    if out.get("ok") and out.get("stdout"):
        try:
            pid = int(out["stdout"].splitlines()[-1].strip())
        except ValueError:
            pass
    return {"policy_id": pid, **out}


def _cleanup_f013(org_slug: str) -> None:
    script = (
        "from policy.models import Policy, Rule; "
        f"qs=Policy.objects.filter(code__startswith='{VAL_PREFIX}'); "
        "Rule.objects.filter(policy__in=qs).delete(); qs.delete(); print('ok')"
    )
    _django_shell(script)


def _policy_test_no_domain(client: httpx.Client, tok: str, policy_id: int) -> dict:
    """F-013: policy_id without policy_domain must NOT 404."""
    r = client.post(
        f"{CONTROL}/api/policies/test/",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "policy_id": policy_id,
            "prompt": SSN_TEXT,
            "input_args": {"message": SSN_TEXT},
        },
        timeout=30,
    )
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return {
        "status": r.status_code,
        "action": data.get("action"),
        "detail": data.get("detail"),
        "pass": r.status_code == 200 and data.get("action") == "block",
    }


def _cross_tenant(client: httpx.Client, orgs: list[dict]) -> list[dict]:
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


def _sandbox_posture() -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ralph/mcp_validation_iter6_sandbox_posture.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    path = ROOT / "mcp-parallel/findings/mcp-validation/iter6-sandbox-posture.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    return {"exit_code": proc.returncode, "ok": data.get("ok"), "report": data}


def _run_regression(script: str) -> dict:
    time.sleep(20)  # login throttle cooldown before nested harness logins
    proc = subprocess.run(
        [str(ROOT / "gateway/.venv/bin/python"), str(ROOT / f"scripts/ralph/{script}")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {"script": script, "ok": proc.returncode == 0, "exit_code": proc.returncode, "tail": (proc.stdout or "")[-500:]}


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _container_sha(container: str, inner: str) -> str | None:
    proc = subprocess.run(
        ["docker", "exec", container, "sha256sum", inner],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.split()[0] if proc.stdout.strip() else None


def main() -> int:
    mf = json.loads(MANIFEST.read_text())
    orgs = mf["orgs"]
    report: dict = {
        "f013": {},
        "multi_org": [],
        "org_scan_isolation": {},
        "f005": [],
        "cross_tenant": [],
        "sandbox": {},
        "regressions": {},
        "image_parity": {},
        "checks": {},
        "ok": False,
    }

    with httpx.Client() as client:
        token_cache: dict[str, str] = {}

        def tok_for(email: str) -> str:
            if email not in token_cache:
                token_cache[email] = _login(client, email)
            return token_cache[email]

        # --- Preflight: restore 0 scan-control baseline on every org ---
        report["preflight"] = {}
        for org in orgs:
            deleted = _delete_all_scan_controls(client, tok_for(org["email"]), org["slug"])
            report["preflight"][org["slug"]] = {"deleted_scan_controls": deleted}
        time.sleep(1.5)

        # --- F-013 live ---
        _cleanup_f013("zeroshield")
        created = _create_f013_policy("zeroshield")
        if created.get("policy_id"):
            report["f013"] = _policy_test_no_domain(client, tok_for("admin@zeroshield.io"), created["policy_id"])
            report["f013"]["policy_id"] = created["policy_id"]
        else:
            report["f013"] = {"pass": False, "error": "policy_create_failed", **created}
        _cleanup_f013("zeroshield")

        # --- Multi-org zero-controls baseline ---
        for org in orgs:
            slug = org["slug"]
            tok = tok_for(org["email"])
            sc = client.get(
                f"{CONTROL}/api/mcp-connector/scan-controls/",
                headers={"Authorization": f"Bearer {tok}"},
                timeout=30,
            )
            n_rows = len(sc.json()) if isinstance(sc.json(), list) else 0
            echo = _gw_echo(client, slug, org["gateway_key"])
            time.sleep(1.0)
            ev = _latest_event(client, tok)
            row = {
                "slug": slug,
                "scan_rows": n_rows,
                "raw_ssn": echo.get("raw_ssn_visible"),
                "decision": (ev or {}).get("decision"),
                "compliance_tags": (ev or {}).get("compliance_tags") or [],
                "pass": (
                    n_rows == 0
                    and echo.get("raw_ssn_visible") is True
                    and (ev or {}).get("decision") == "scan_skipped"
                    and len((ev or {}).get("compliance_tags") or []) == 0
                ),
            }
            report["multi_org"].append(row)

        # --- Org-scoped scan isolation: block only on org-a ---
        org_a = next(o for o in orgs if o["slug"] == "org-a")
        org_b = next(o for o in orgs if o["slug"] == "org-b")
        tok_a = tok_for(org_a["email"])
        _delete_marked_controls(client, tok_a, "org-a")
        cid = _create_scan_block(client, tok_a, "org-a")
        time.sleep(1.5)
        blocked_a = _gw_echo(client, "org-a", org_a["gateway_key"])
        raw_b = _gw_echo(client, "org-b", org_b["gateway_key"])
        _delete_scan_control(client, tok_a, cid, "org-a")
        report["org_scan_isolation"] = {
            "org_a_blocked": blocked_a.get("blocked"),
            "org_b_raw": raw_b.get("raw_ssn_visible"),
            "pass": blocked_a.get("blocked") is True and raw_b.get("raw_ssn_visible") is True,
        }

        # --- F-005 cross-plane per org (gateway raw, control may mask if policies on) ---
        for org in orgs:
            tok = tok_for(org["email"])
            gw = _gw_echo(client, org["slug"], org["gateway_key"])
            ctl = _ctl_echo(client, tok)
            report["f005"].append({
                "slug": org["slug"],
                "gateway_raw": gw.get("raw_ssn_visible"),
                "control_masked": ctl.get("masked"),
                "pass": gw.get("raw_ssn_visible") is True,
            })

        report["cross_tenant"] = _cross_tenant(client, orgs)

    report["sandbox"] = _sandbox_posture()

    ev_path = ROOT / "control/ai_mesh_control/policy/evaluation_views.py"
    report["image_parity"]["control_evaluation_views"] = {
        "workspace": _file_sha(ev_path),
        "container": _container_sha("ai_mesh_firewall-control-1", "/app/control/ai_mesh_control/policy/evaluation_views.py"),
        "match": False,
    }
    ws = report["image_parity"]["control_evaluation_views"]["workspace"]
    ct = report["image_parity"]["control_evaluation_views"]["container"]
    report["image_parity"]["control_evaluation_views"]["match"] = ws == ct and ws is not None

    with httpx.Client() as client:
        for org in orgs:
            try:
                tok = _login(client, org["email"])
            except Exception:
                time.sleep(10)
                tok = _login(client, org["email"])
            _delete_all_scan_controls(client, tok, org["slug"])
        time.sleep(1.0)
    report["regressions"]["iter12"] = _run_regression("mcp_validation_iter12_closeout.py")

    with httpx.Client() as client:
        report["postflight"] = {}
        for org in orgs:
            try:
                tok = _login(client, org["email"])
            except Exception:
                time.sleep(10)
                tok = _login(client, org["email"])
            deleted = _delete_all_scan_controls(client, tok, org["slug"])
            report["postflight"][org["slug"]] = {"deleted_scan_controls": deleted}

    report["checks"] = {
        "f013_pass": report["f013"].get("pass"),
        "multi_org_pass": all(r["pass"] for r in report["multi_org"]),
        "scan_isolation_pass": report["org_scan_isolation"].get("pass"),
        "f005_pass": all(r["pass"] for r in report["f005"]),
        "cross_tenant_pass": all(c["pass"] for c in report["cross_tenant"]),
        "sandbox_pass": report["sandbox"].get("ok"),
        "regression_pass": report["regressions"]["iter12"].get("ok"),
        "f013_image_parity": report["image_parity"]["control_evaluation_views"]["match"],
    }
    report["ok"] = all(report["checks"].values())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps({"ok": report["ok"], "checks": report["checks"]}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
