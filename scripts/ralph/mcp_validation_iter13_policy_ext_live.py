#!/usr/bin/env python3
"""Iteration 13 — policy combinatorial matrix + live ext-proxy via mcp-stub.

Writes: mcp-parallel/findings/mcp-validation/iter13-policy-ext-live.json
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
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter13-policy-ext-live.json"
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
EMAIL = os.environ.get("AUDIT_EMAIL", "admin@zeroshield.io")
PASS = os.environ.get("AUDIT_PASS", "Adm1n!Pass#2024")
ORG = os.environ.get("ORG", "zeroshield")
SERVER = os.environ.get("SERVER", "everything-1")
SSN = "123-45-6789"
SSN_TEXT = f"patient SSN is {SSN}"
MARK = 8888
VAL_PREFIX = "VAL_ITER13_"
EXT_HOST = os.environ.get("MCP_EXT_VALIDATION_HOST", "mcp-stub:9999")


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


def _gw_echo(client: httpx.Client, key: str, message: str = SSN_TEXT) -> dict:
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
        "masked": SSN not in text and "SSN" in message,
        "raw_ssn_visible": SSN in text,
        "body": body,
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
    if body.get("blocked"):
        text = body.get("detail") or ""
    elif isinstance(body.get("result"), dict):
        text = _egress_text({"result": body["result"]})
    else:
        text = _egress_text(body)
    return {
        "status": r.status_code,
        "decision": body.get("decision"),
        "text": text,
        "blocked": body.get("blocked") is True or "[BLOCKED]" in text,
        "masked": SSN not in text and "SSN" in message,
        "raw_ssn_visible": SSN in text,
        "compliance_tags": body.get("compliance_tags") or [],
    }


def _policy_test(client: httpx.Client, tok: str, *, input_args=None, output_data=None, policy_id=None) -> dict:
    body = {
        "prompt": f"tool:echo {SSN_TEXT}",
        "policy_domain": "mcp",
        "input_args": input_args or {"message": SSN_TEXT},
    }
    if output_data is not None:
        body["output_data"] = output_data
    if policy_id is not None:
        body["policy_id"] = policy_id
    r = client.post(
        f"{CONTROL}/api/policies/test/",
        headers={"Authorization": f"Bearer {tok}"},
        json=body,
        timeout=30,
    )
    data = r.json() if r.is_success else {"error": r.text[:300]}
    return {"status": r.status_code, **data}


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


def _bulk_disable_mcp_policies() -> dict:
    script = (
        "from policy.models import Policy; from auth.models import Organization; "
        "from django.db.models import Q; "
        f"org=Organization.objects.get(slug='{ORG}'); "
        "n=Policy.objects.filter(policy_domain='mcp')"
        ".filter(Q(organization=org)|Q(organization__isnull=True)).update(enabled=False); "
        "print(n)"
    )
    return _django_shell(script)


def _bulk_enable_mcp_policies() -> dict:
    script = (
        "from policy.models import Policy; from auth.models import Organization; "
        "from django.db.models import Q; "
        f"org=Organization.objects.get(slug='{ORG}'); "
        "n=Policy.objects.filter(policy_domain='mcp')"
        ".filter(Q(organization=org)|Q(organization__isnull=True)).update(enabled=True); "
        "print(n)"
    )
    return _django_shell(script)


def _cleanup_val_policies() -> None:
    script = (
        "from policy.models import Policy, Rule; "
        f"qs=Policy.objects.filter(code__startswith='{VAL_PREFIX}'); "
        "Rule.objects.filter(policy__in=qs).delete(); n=qs.delete()[0]; print(n)"
    )
    _django_shell(script)


def _create_val_policy(code_suffix: str, action: str, direction: str) -> dict:
    code = f"{VAL_PREFIX}{code_suffix}"
    script = (
        "from policy.models import Policy, Rule; from auth.models import Organization; "
        f"org=Organization.objects.get(slug='{ORG}'); "
        f"code='{code}'; action='{action}'; direction='{direction}'; "
        "Policy.objects.filter(code=code).delete(); "
        "p=Policy.objects.create(code=code, name=code, policy_domain='mcp', "
        "organization=org, category='pii', severity='HIGH', enabled=True, priority=9999); "
        "Rule.objects.create(policy=p, name=code+'-rule', rule_type='regex', "
        "condition={'preset':'us_ssn','direction':direction,'scope':'entire'}, "
        "action=action, redaction_config={}, priority=10, enabled=True); "
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


def _delete_all_scan_controls(client: httpx.Client, tok: str) -> None:
    r = client.get(f"{CONTROL}/api/mcp-connector/scan-controls/", headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    for row in rows:
        if row.get("priority") in (MARK, MARK + 1):
            client.delete(
                f"{CONTROL}/api/mcp-connector/scan-controls/{row['id']}/",
                headers={"Authorization": f"Bearer {tok}"},
                timeout=30,
            )
    _bust_scan_cache()


def _ext_proxy_echo(client: httpx.Client, key: str, msg: str) -> dict:
    url = f"{GATEWAY}/v1/mcp/ext-proxy/{EXT_HOST}/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"msg": msg}},
    }
    r = client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=60)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:500]}
    text = _egress_text(body if isinstance(body, dict) else {})
    err = body.get("error") if isinstance(body, dict) else None
    err_msg = err.get("message", "") if isinstance(err, dict) else ""
    return {
        "status": r.status_code,
        "host": EXT_HOST,
        "text": text,
        "raw_ssn_visible": SSN in text,
        "masked": SSN not in text and SSN in msg,
        "blocked_inbound": "credential" in err_msg.lower(),
        "error_message": err_msg,
        "body": body,
    }


def _gateway_ext_env() -> dict:
    proc = subprocess.run(
        ["docker", "exec", "ai_mesh_firewall-gateway-1", "printenv"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    env = {}
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.startswith("MCP_EXT_"):
                    env[k] = v
    return env


def main() -> int:
    report: dict = {"policy_matrix": [], "cross_plane": {}, "ext_live": {}, "env": {}}
    mf = json.loads(MANIFEST.read_text())
    key = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)

    with httpx.Client() as client:
        tok = _login(client)
        _delete_all_scan_controls(client, tok)
        _cleanup_val_policies()
        _bulk_disable_mcp_policies()
        time.sleep(1)

        # Baseline: 0 scan controls, 0 MCP policies → gateway raw
        report["cross_plane"]["baseline"] = {
            "gateway": _gw_echo(client, key),
            "control": _ctl_echo(client, tok),
        }
        report["cross_plane"]["baseline_pass"] = (
            report["cross_plane"]["baseline"]["gateway"].get("raw_ssn_visible")
            and report["cross_plane"]["baseline"]["control"].get("raw_ssn_visible")
        )

        cases = [
            ("input_redact", "redact", "input", {"action": "redact"}, {"masked": True}),
            ("output_redact", "redact", "output", {"action": "redact"}, {"masked": True}),
            ("both_block", "block", "both", {"action": "block"}, {"status": 403}),
            ("both_monitor", "monitor", "both", {"action": "monitor"}, {"decision": "monitor"}),
        ]

        for suffix, action, direction, expect_test, expect_live in cases:
            created = _create_val_policy(suffix, action, direction)
            time.sleep(0.5)
            pid = created.get("policy_id")
            dry = _policy_test(
                client,
                tok,
                policy_id=pid,
                input_args={"message": SSN_TEXT},
                output_data={"message": SSN_TEXT} if direction in ("output", "both") else None,
            )
            ctl = _ctl_echo(client, tok)
            gw = _gw_echo(client, key)
            row = {
                "case": suffix,
                "action": action,
                "direction": direction,
                "policy_id": pid,
                "dry_run": dry,
                "control_live": ctl,
                "gateway_live": gw,
                "pass": dry.get("action") == expect_test["action"],
            }
            for k, v in expect_live.items():
                if ctl.get(k) != v:
                    row["pass"] = False
                    row.setdefault("failures", []).append(f"control.{k} expected {v} got {ctl.get(k)}")
            # Gateway scan matrix independent (F-005): stays raw @ 0 scan controls
            if not gw.get("raw_ssn_visible"):
                row.setdefault("gateway_note", "gateway masked despite 0 scan controls — unexpected")
            report["policy_matrix"].append(row)
            _cleanup_val_policies()
            time.sleep(0.5)

        # Block beats redact precedence
        script = (
            "from policy.models import Policy, Rule; from auth.models import Organization; "
            f"org=Organization.objects.get(slug='{ORG}'); code='{VAL_PREFIX}PRECEDENCE'; "
            "Policy.objects.filter(code=code).delete(); "
            "p=Policy.objects.create(code=code, name=code, policy_domain='mcp', organization=org, "
            "category='pii', severity='HIGH', enabled=True, priority=9999); "
            "Rule.objects.create(policy=p, name='redact', rule_type='regex', "
            "condition={'preset':'us_ssn','direction':'both','scope':'entire'}, action='redact', "
            "redaction_config={}, priority=1, enabled=True); "
            "Rule.objects.create(policy=p, name='block', rule_type='regex', "
            "condition={'preset':'us_ssn','direction':'both','scope':'entire'}, action='block', "
            "redaction_config={}, priority=10, enabled=True); print(p.id)"
        )
        prec = _django_shell(script)
        pid = int(prec["stdout"].splitlines()[-1]) if prec.get("ok") else None
        dry_prec = _policy_test(
            client,
            tok,
            policy_id=pid,
            input_args={"message": SSN_TEXT},
            output_data={"message": SSN_TEXT},
        )
        report["policy_matrix"].append({
            "case": "block_beats_redact",
            "dry_run": dry_prec,
            "pass": dry_prec.get("action") == "block",
        })
        _cleanup_val_policies()

        _bulk_enable_mcp_policies()
        time.sleep(1)

        # Live ext-proxy via mcp-stub (requires MCP_EXT_* env on gateway)
        report["env"] = _gateway_ext_env()
        ext_msg = SSN_TEXT
        ext_cred = f"key AKIAIOSFODNN7EXAMPLE in {ext_msg}"
        report["ext_live"] = {
            "echo_ssn": _ext_proxy_echo(client, key, ext_msg),
            "echo_cred": _ext_proxy_echo(client, key, ext_cred),
            "env": report["env"],
        }
        report["ext_live"]["pass"] = (
            EXT_HOST in (report["env"].get("MCP_EXT_ALLOWED_DOMAINS") or "")
            and (
                report["ext_live"]["echo_ssn"].get("masked")
                or report["ext_live"]["echo_ssn"].get("raw_ssn_visible") is False
            )
            and (
                report["ext_live"]["echo_cred"].get("blocked_inbound")
                or "AKIA" not in json.dumps(report["ext_live"]["echo_cred"].get("body", {}))
            )
        )

    report["checks"] = {
        "policy_matrix_pass": all(r.get("pass") for r in report["policy_matrix"]),
        "baseline_pass": report["cross_plane"].get("baseline_pass"),
        "ext_live_pass": report["ext_live"].get("pass"),
    }
    report["ok"] = (
        report["checks"]["policy_matrix_pass"]
        and report["checks"]["baseline_pass"]
        and report["checks"]["ext_live_pass"]
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps({"ok": report["ok"], "checks": report["checks"]}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
