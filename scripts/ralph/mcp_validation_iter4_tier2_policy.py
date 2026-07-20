#!/usr/bin/env python3
"""Iteration 4 — Tier-2 gating, audit scan_trace, policy vs scan plane, SDK smoke.

Writes: mcp-parallel/findings/mcp-validation/iter4-tier2-policy-ui.json
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
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter4-tier2-policy-ui.json"
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
EMAIL = os.environ.get("AUDIT_EMAIL", "admin@zeroshield.io")
PASS = os.environ.get("AUDIT_PASS", "Adm1n!Pass#2024")
ORG = os.environ.get("ORG", "zeroshield")
SERVER = os.environ.get("SERVER", "everything-1")
MARK = 7777
SSN = "SSN 123-45-6789"


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


def _internal_key() -> str:
    return subprocess.check_output(
        ["docker", "exec", "ai_mesh_firewall-gateway-1", "printenv", "GATEWAY_INTERNAL_API_KEY"],
        text=True,
    ).strip()


def _trace_stages(meta: dict) -> list[str]:
    trace = meta.get("scan_trace") or []
    return [t.get("scan_stage") or t.get("tier") or "?" for t in trace if isinstance(t, dict)]


def _latest_echo_event(client: httpx.Client, tok: str, marker: str) -> dict | None:
    """Find newest echo MCPEvent whose metadata mentions marker (best-effort)."""
    r = client.get(
        f"{CONTROL}/api/mcp-connector/events/",
        headers={"Authorization": f"Bearer {tok}"},
        params={"tool": "echo", "limit": 30, "hours": 1},
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    for ev in rows:
        meta = ev.get("metadata") or {}
        if marker in json.dumps(meta, default=str):
            return ev
        if ev.get("decision") in ("scan_skipped", "allow", "monitor", "redact", "block"):
            # fallback: first recent echo after our window
            pass
    return rows[0] if rows else None


def _gw_echo(client: httpx.Client, key: str, message: str) -> dict:
    rid = f"iter4-{uuid.uuid4().hex[:8]}"
    payload = {
        "jsonrpc": "2.0",
        "id": rid,
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
    content = ((body.get("result") or {}).get("content") or [])
    text = content[0].get("text", "") if content else ""
    return {
        "request_id": rid,
        "status": r.status_code,
        "latency_ms": ms,
        "egress_text": text,
        "raw_ssn_visible": "123-45-6789" in text,
        "masked": "***" in text or "REDACTED" in text,
    }


def _create_control(client: httpx.Client, tok: str, **fields) -> str:
    body = {
        "scope_type": fields.get("scope_type", "org"),
        "direction": fields.get("direction", "both"),
        "tier": fields.get("tier", "tier1"),
        "enabled": fields.get("enabled", True),
        "action": fields.get("action", "monitor"),
        "priority": fields.get("priority", MARK),
    }
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


def _delete_marked(client: httpx.Client, tok: str) -> None:
    r = client.get(
        f"{CONTROL}/api/mcp-connector/scan-controls/",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    for row in rows:
        if row.get("priority") == MARK:
            _delete_control(client, tok, row["id"])


def _set_tier2_org(client: httpx.Client, tok: str, value: bool | None) -> None:
    """PUT firewall config mcp_tier2_enabled (null|true|false)."""
    r = client.put(
        f"{CONTROL}/api/firewall/config/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"mcp_tier2_enabled": value},
        timeout=30,
    )
    r.raise_for_status()
    _bust_scan_cache()


def _run_sdk_smoke() -> dict:
    venv = ROOT / "gateway" / ".venv" / "bin" / "python"
    if not venv.exists():
        return {"ok": False, "skipped": True, "reason": "gateway venv missing"}
    proc = subprocess.run(
        [str(venv), "-m", "pytest", "ai_mesh_gateway/tests/test_openai_sdk_compat.py", "-q", "--tb=line"],
        cwd=ROOT / "gateway",
        capture_output=True,
        text=True,
        timeout=300,
    )
    tail = (proc.stdout or "") + (proc.stderr or "")
    passed = proc.returncode == 0
    return {
        "ok": passed,
        "exit_code": proc.returncode,
        "summary_line": tail.strip().splitlines()[-1] if tail.strip() else "",
    }


def main() -> int:
    mf = json.loads(MANIFEST.read_text())
    key = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)
    ih = {"X-Gateway-Internal-Key": _internal_key(), "X-Gateway-Auth": "true", "X-Org-Slug": ORG}
    report: dict = {"org": ORG, "server": SERVER, "checks": {}, "cases": []}
    created: list[str] = []
    tier2_restore: bool | None = None

    with httpx.Client() as client:
        tok_r = client.post(f"{CONTROL}/api/auth/token/", json={"email": EMAIL, "password": PASS}, timeout=30)
        tok_r.raise_for_status()
        tok = tok_r.json()["access"]

        _delete_marked(client, tok)

        # Snapshot org tier2 setting for restore
        fw = client.get(f"{CONTROL}/api/firewall/config/", headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        if fw.status_code == 200:
            tier2_restore = fw.json().get("mcp_tier2_enabled")

        # ── A: zero controls → scan_skipped, no tier stages ──
        sc = client.get(
            f"{CONTROL}/api/mcp-connector/scan-controls/",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=30,
        )
        n_rows = len(sc.json()) if isinstance(sc.json(), list) else 0
        marker_a = f"ITER4-A-{uuid.uuid4().hex[:6]}"
        got_a = _gw_echo(client, key, f"{marker_a} {SSN}")
        time.sleep(2)
        ev_a = _latest_echo_event(client, tok, marker_a)
        meta_a = (ev_a or {}).get("metadata") or {}
        stages_a = _trace_stages(meta_a)
        case_a = {
            "name": "zero_controls_scan_skipped",
            "scan_rows": n_rows,
            "decision": (ev_a or {}).get("decision"),
            "scan_skipped": meta_a.get("scan_skipped"),
            "stages": stages_a,
            "egress": got_a,
            "pass": (
                n_rows == 0
                and (ev_a or {}).get("decision") == "scan_skipped"
                and "tier1" not in stages_a
                and "tier2" not in stages_a
                and got_a.get("raw_ssn_visible") is True
            ),
        }
        report["cases"].append(case_a)

        # ── B: tier1-only row → tier1 runs, tier2 absent ──
        cid_b = _create_control(client, tok, tier="tier1", action="monitor", direction="both")
        created.append(cid_b)
        time.sleep(2)
        en = client.get(
            f"{CONTROL}/api/mcp-connector/internal/enabled-tools/?server_slug={SERVER}",
            headers=ih,
            timeout=30,
        )
        en.raise_for_status()
        eff = en.json()
        t2_in = (eff.get("effective_scan_controls") or {}).get("tier2_input") or {}
        marker_b = f"ITER4-B-{uuid.uuid4().hex[:6]}"
        got_b = _gw_echo(client, key, f"{marker_b} clean text")
        time.sleep(2)
        ev_b = _latest_echo_event(client, tok, marker_b)
        meta_b = (ev_b or {}).get("metadata") or {}
        stages_b = _trace_stages(meta_b)
        case_b = {
            "name": "tier1_only_no_tier2",
            "tier2_input_enabled": t2_in.get("enabled"),
            "decision": (ev_b or {}).get("decision"),
            "stages": stages_b,
            "pass": (
                eff.get("scan_controls_configured") is True
                and t2_in.get("enabled") is False
                and "tier1" in stages_b
                and "tier2" not in stages_b
                and (ev_b or {}).get("decision") != "scan_skipped"
            ),
        }
        report["cases"].append(case_b)

        # ── C: tier2 row + org tier2 disabled → tier2_skipped in trace ──
        _set_tier2_org(client, tok, False)
        cid_c = _create_control(client, tok, tier="tier2", action="monitor", direction="input")
        created.append(cid_c)
        time.sleep(2)
        _bust_scan_cache()
        marker_c = f"ITER4-C-{uuid.uuid4().hex[:6]}"
        _gw_echo(client, key, f"{marker_c} clean")
        time.sleep(2)
        ev_c = _latest_echo_event(client, tok, marker_c)
        meta_c = (ev_c or {}).get("metadata") or {}
        stages_c = _trace_stages(meta_c)
        reasons = [t.get("reason") for t in (meta_c.get("scan_trace") or []) if isinstance(t, dict)]
        case_c = {
            "name": "tier2_row_but_org_disabled",
            "mcp_tier2_enabled": False,
            "stages": stages_c,
            "reasons": reasons,
            "pass": "tier2_skipped" in stages_c or "org_mcp_tier2_disabled" in reasons,
        }
        report["cases"].append(case_c)

        # ── D: control tools/call still reachable (policy plane separate) ──
        tr = client.post(
            f"{CONTROL}/api/mcp-connector/tools/call/",
            headers={"Authorization": f"Bearer {tok}"},
            json={"name": "echo", "arguments": {"message": "iter4-control-path"}, "server_slug": SERVER},
            timeout=90,
        )
        case_d = {
            "name": "control_tools_call_live",
            "status": tr.status_code,
            "has_decision": "decision" in (tr.json() if tr.headers.get("content-type", "").startswith("application/json") else {}),
            "pass": tr.status_code in (200, 403),
        }
        report["cases"].append(case_d)

        # ── E: enabled-tools contract ──
        case_e = {
            "name": "enabled_tools_contract",
            "scan_controls_configured": eff.get("scan_controls_configured"),
            "mcp_tier2_enabled": eff.get("mcp_tier2_enabled"),
            "pass": "scan_controls_configured" in eff and "mcp_tier2_enabled" in eff,
        }
        report["cases"].append(case_e)

        # cleanup scan controls
        for cid in created:
            _delete_control(client, tok, cid)
        if tier2_restore is not None:
            _set_tier2_org(client, tok, tier2_restore)

    report["sdk_smoke"] = _run_sdk_smoke()
    report["f002_ext_proxy"] = {
        "finding": "F-002",
        "status": "DOCUMENTED",
        "detail": "ext_mcp_proxy passes enabled_info=None → scan_controls gate never fires; static tier1 floors still run by design",
        "code": "gateway/ai_mesh_gateway/mcp_proxy.py ext_mcp_proxy ~L1989",
    }
    report["checks"]["all_cases_pass"] = all(c.get("pass") for c in report["cases"])
    report["checks"]["sdk_pass"] = report["sdk_smoke"].get("ok")
    report["ok"] = report["checks"]["all_cases_pass"] and report["checks"]["sdk_pass"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
