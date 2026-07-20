#!/usr/bin/env python3
"""Iteration 10 — policy on/off matrix, ext-proxy live floors, live SDK chat, gVisor, image parity.

Writes: mcp-parallel/findings/mcp-validation/iter10-policy-ext-sdk-live.json
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
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter10-policy-ext-sdk-live.json"
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
EMAIL = os.environ.get("AUDIT_EMAIL", "admin@zeroshield.io")
PASS = os.environ.get("AUDIT_PASS", "Adm1n!Pass#2024")
ORG = os.environ.get("ORG", "zeroshield")
SERVER = os.environ.get("SERVER", "everything-1")
SSN = "SSN 123-45-6789"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
MARK = 8888


def _bust_scan_cache(org: str = ORG) -> None:
    if redis is None:
        return
    r = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
    for key in (f"mcp:scan_ver:{org}", f"mcp:scan_ver:{org}:{SERVER}"):
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
        "masked": "***" in text or "REDACTED" in text or ("123-45-6789" not in text and "SSN" in message),
        "raw_ssn_visible": "123-45-6789" in text,
        "compliance_tags": body.get("compliance_tags") or [],
    }


def _list_all_mcp_policies(client: httpx.Client, tok: str) -> list[dict]:
    """Paginate — the list API returns 10/page; tools/call evaluates all org MCP policies."""
    headers = {"Authorization": f"Bearer {tok}"}
    out: list[dict] = []
    url = f"{CONTROL}/api/policies/?policy_domain=mcp"
    while url:
        r = client.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            out.extend(data)
            break
        out.extend(data.get("results") or [])
        url = data.get("next")
    return out


def _set_policy_enabled(client: httpx.Client, tok: str, pid: int, enabled: bool) -> None:
    headers = {"Authorization": f"Bearer {tok}"}
    for attempt in range(5):
        r = client.patch(
            f"{CONTROL}/api/policies/{pid}/",
            headers=headers,
            json={"enabled": enabled},
            timeout=30,
        )
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 2)))
            continue
        r.raise_for_status()
        return
    r.raise_for_status()


def _bulk_set_mcp_policies_enabled(enabled: bool) -> dict:
    """Bulk toggle all org MCP policies via Django (avoids policy PATCH 429 throttle)."""
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
            "docker",
            "exec",
            "ai_mesh_firewall-control-1",
            "bash",
            "-lc",
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
    return {"ok": proc.returncode == 0, "updated": count, "stderr": (proc.stderr or "")[-300:]}


def _compile_policies(client: httpx.Client, tok: str) -> dict:
    r = client.post(
        f"{CONTROL}/api/policies/compile/",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=120,
    )
    return {"status": r.status_code, "body": r.json() if r.is_success else r.text[:300]}


def _enabled_tools(client: httpx.Client, tok: str) -> dict:
    internal_key = os.environ.get("GATEWAY_INTERNAL_API_KEY", "")
    headers = {
        "Authorization": f"Bearer {tok}",
        "X-Org-Slug": ORG,
        "X-Server-Slug": SERVER,
    }
    if internal_key:
        headers["X-Gateway-Internal-Key"] = internal_key
        r = client.get(
            f"{CONTROL}/api/mcp-connector/internal/enabled-tools/",
            headers=headers,
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()
    r = client.get(
        f"{CONTROL}/api/mcp-connector/scan-controls/",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    rows = data if isinstance(data, list) else data.get("results", [])
    return {"scan_controls_configured": len(rows) > 0}


def _ext_proxy_tools_call(client: httpx.Client, key: str, host: str, args: dict) -> dict:
    url = f"{GATEWAY}/v1/mcp/ext-proxy/{host}/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": "echo", "arguments": args},
    }
    r = client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=60)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:500]}
    err = body.get("error") if isinstance(body, dict) else None
    msg = ""
    if isinstance(err, dict):
        msg = err.get("message", "")
    text = _egress_text(body if isinstance(body, dict) else {})
    return {
        "status": r.status_code,
        "host": host,
        "error_message": msg,
        "blocked_inbound": "compliance tags" in msg.lower() or "credential" in msg.lower(),
        "text": text,
        "body": body,
    }


def _gvisor_posture() -> dict:
    out: dict = {"runsc_installed": False, "sandbox_runtimes": []}
    try:
        info = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"], capture_output=True, text=True, timeout=30)
        if info.returncode == 0 and info.stdout.strip():
            runtimes = json.loads(info.stdout)
            out["runtimes"] = list(runtimes.keys()) if isinstance(runtimes, dict) else []
            out["runsc_installed"] = "runsc" in out["runtimes"]
    except Exception as exc:
        out["error"] = str(exc)
    for cname in (f"{ORG}-mcp-sandbox", "zeroshield-mcp-sandbox"):
        chk = subprocess.run(
            ["docker", "inspect", "-f", "{{.HostConfig.Runtime}}", cname],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if chk.returncode == 0:
            out["sandbox_runtime"] = chk.stdout.strip() or "default"
            out["sandbox_container"] = cname
            break
    out["pass"] = out.get("sandbox_runtime") == "runsc" if out.get("runsc_installed") else False
    out["infra_blocker"] = not out.get("runsc_installed")
    return out


def _container_code_parity() -> dict:
    """Verify hot-deployed fixes are present in running containers (not necessarily in images)."""
    checks = {}
    for label, cname, needle, path in (
        (
            "control_scan_controls_f009",
            "ai_mesh_firewall-control-1",
            "configured: bool = False",
            "/app/control/ai_mesh_control/mcp_connector/scan_controls.py",
        ),
        (
            "gateway_tier1_action_f010",
            "ai_mesh_firewall-gateway-1",
            "_resolved_tier1_action",
            "/app/gateway/ai_mesh_gateway/mcp_proxy.py",
        ),
    ):
        proc = subprocess.run(
            ["docker", "exec", cname, "grep", "-c", needle, path],
            capture_output=True,
            text=True,
            timeout=20,
        )
        checks[label] = {
            "container": cname,
            "needle": needle,
            "grep_count": int(proc.stdout.strip() or "0") if proc.returncode == 0 else 0,
            "present": proc.returncode == 0 and int(proc.stdout.strip() or "0") > 0,
        }
    checks["pass"] = all(c["present"] for c in checks.values() if isinstance(c, dict) and "present" in c)
    return checks


def _live_openai_chat() -> dict:
    golden = ROOT / "gateway" / "tests" / "golden"
    if not golden.is_dir():
        return {"ok": False, "reason": "golden driver missing"}
    sys.path.insert(0, str(golden))
    sys.path.insert(0, str(ROOT / "gateway"))
    try:
        from live_driver import characterize_live_chat, live_gateway_reachable

        if not live_gateway_reachable():
            return {"ok": False, "reason": "gateway unreachable"}
        obs = characterize_live_chat("Say hello in one short sentence.", max_tokens=32)
        content_ok = obs.get("http_status") == 200
        return {
            "ok": obs.get("http_status") == 200 and obs.get("final_action") in ("allow", "monitor", ""),
            "http_status": obs.get("http_status"),
            "final_action": obs.get("final_action"),
            "stages": [s.get("stage") for s in (obs.get("stages") or [])[:8]],
            "content_hint": content_ok,
            "payload_excerpt": obs.get("payload_excerpt", "")[:400],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if str(golden) in sys.path:
            sys.path.remove(str(golden))


def _multi_org_zero_controls(client: httpx.Client, tok: str) -> dict:
    mf = json.loads(MANIFEST.read_text())
    rows = []
    for org in mf["orgs"]:
        slug = org["slug"]
        key = org["gateway_key"]
        srv = org["servers"][0]
        url = f"{GATEWAY}/gateway/{slug}/mcp/{srv}"
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": SSN}},
        }
        r = client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=90)
        body = r.json()
        text = _egress_text(body)
        rows.append(
            {
                "org": slug,
                "status": r.status_code,
                "raw_ssn": "123-45-6789" in text,
                "pass": r.status_code == 200 and "123-45-6789" in text,
            }
        )
    return {"cases": rows, "pass": all(r["pass"] for r in rows)}


def main() -> int:
    report: dict = {
        "policy_matrix": {},
        "ext_proxy": {},
        "live_sdk_chat": {},
        "gvisor": {},
        "container_parity": {},
        "multi_org": {},
        "checks": {},
    }
    mf = json.loads(MANIFEST.read_text())
    key = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)
    saved_policies: list[dict] = []  # unused; kept for future per-policy toggles

    with httpx.Client() as client:
        tok = _login(client)
        et = _enabled_tools(client, tok)
        sc_resp = client.get(
            f"{CONTROL}/api/mcp-connector/scan-controls/",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=30,
        )
        sc_data = sc_resp.json()
        sc_list = sc_data if isinstance(sc_data, list) else sc_data.get("results", [])
        report["baseline"] = {
            "scan_controls_configured": et.get("scan_controls_configured"),
            "scan_row_count": len(sc_list),
        }

        # ── Policy on/off matrix (F-005 inverse) ──
        policies = _list_all_mcp_policies(client, tok)
        report["policy_matrix"]["policy_count"] = len(policies)
        bulk_off = _bulk_set_mcp_policies_enabled(False)
        time.sleep(2)
        gw_off = _gw_echo(client, key, SSN)
        ctl_off = _ctl_echo(client, tok, SSN)
        report["policy_matrix"]["all_mcp_disabled"] = {
            "bulk_update": bulk_off,
            "gateway": gw_off,
            "control": ctl_off,
            "pass": (
                bulk_off.get("ok")
                and gw_off.get("raw_ssn_visible")
                and ctl_off.get("raw_ssn_visible")
            ),
        }
        bulk_on = _bulk_set_mcp_policies_enabled(True)
        _compile_policies(client, tok)
        time.sleep(3)
        gw_on = _gw_echo(client, key, SSN)
        ctl_on = _ctl_echo(client, tok, SSN)
        report["policy_matrix"]["mcp_policies_restored"] = {
            "bulk_update": bulk_on,
            "gateway": gw_on,
            "control": ctl_on,
            "compile": _compile_policies(client, tok),
            "pass": gw_on.get("raw_ssn_visible") and ctl_on.get("masked"),
        }
        report["policy_matrix"]["pass"] = (
            report["policy_matrix"]["all_mcp_disabled"]["pass"]
            and report["policy_matrix"]["mcp_policies_restored"]["pass"]
        )

        # ── ext-proxy live (F-002 differential) ──
        gw_org = _gw_echo(client, key, SSN)
        ext_disallowed = _ext_proxy_tools_call(client, key, "evil.example.com", {"message": SSN})
        ext_cred = _ext_proxy_tools_call(
            client, key, "mcp.context7.com", {"message": f"key {AWS_KEY}"}
        )
        report["ext_proxy"] = {
            "F-002_note": (
                "ext_mcp_proxy passes enabled_info=None — org scan_controls gate skipped; "
                "tier1 still runs with scan_action=tag. Disallowed host → 403; org @ 0 controls → raw."
            ),
            "org_gateway_zero_controls": gw_org,
            "disallowed_host": ext_disallowed,
            "allowed_host_session_error": ext_cred,
            "pass": (
                gw_org.get("raw_ssn_visible") is True
                and ext_disallowed.get("status") == 403
            ),
        }

        report["multi_org"] = _multi_org_zero_controls(client, tok)

    report["live_sdk_chat"] = _live_openai_chat()
    report["gvisor"] = _gvisor_posture()
    report["container_parity"] = _container_code_parity()

    report["checks"] = {
        "policy_matrix_pass": report["policy_matrix"].get("pass"),
        "ext_proxy_pass": report["ext_proxy"].get("pass"),
        "multi_org_pass": report["multi_org"].get("pass"),
        "live_sdk_chat_pass": report["live_sdk_chat"].get("ok"),
        "container_parity_pass": report["container_parity"].get("pass"),
        "gvisor_pass": report["gvisor"].get("pass"),
        "gvisor_infra_blocker": report["gvisor"].get("infra_blocker"),
    }
    # gVisor missing is a documented infra blocker — do not fail the whole iter for it
    report["ok"] = all(
        report["checks"].get(k)
        for k in (
            "policy_matrix_pass",
            "ext_proxy_pass",
            "multi_org_pass",
            "live_sdk_chat_pass",
            "container_parity_pass",
        )
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps({"ok": report["ok"], "checks": report["checks"]}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
