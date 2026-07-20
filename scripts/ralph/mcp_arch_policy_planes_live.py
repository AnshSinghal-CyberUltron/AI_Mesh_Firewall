#!/usr/bin/env python3
"""Live proof: scan-control monitor vs MCP policy redact vs compliance_frameworks.

Writes mcp-parallel/findings/mcp-arch-validation-2026-07-08/enforcement-planes-live.json
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import httpx

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-arch-validation-2026-07-08/enforcement-planes-live.json"
MANIFEST = ROOT / "scripts/ralph/.mcp_scale_manifest.json"
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
EMAIL = os.environ.get("AUDIT_EMAIL", "admin@zeroshield.io")
PASS = os.environ.get("AUDIT_PASS", "Adm1n!Pass#2024")
ORG = os.environ.get("ORG", "zeroshield")
SERVER = os.environ.get("SERVER", "everything-1")
MARK = 8890

_mf = json.loads(MANIFEST.read_text(encoding="utf-8"))
KEY = os.environ.get("GATEWAY_KEY") or next(o["gateway_key"] for o in _mf["orgs"] if o["slug"] == ORG)


def login(c: httpx.Client) -> str:
    r = c.post(f"{CONTROL}/api/auth/token/", json={"email": EMAIL, "password": PASS}, timeout=30)
    r.raise_for_status()
    return r.json()["access"]


def bust_scan_cache() -> None:
    if redis is None:
        return
    r = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
    for key in (f"mcp:scan_ver:{ORG}", f"mcp:scan_ver:{ORG}:{SERVER}"):
        try:
            r.incr(key)
        except redis.ResponseError:
            # Recover from a corrupted non-integer value (never SET timestamps here).
            r.delete(key)
            r.incr(key)


def gw_echo(c: httpx.Client, message: str, req_id: str | None = None) -> dict:
    rid = req_id or f"pl-{uuid.uuid4().hex[:8]}"
    payload = {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": message}},
    }
    r = c.post(
        f"{GATEWAY}/gateway/{ORG}/mcp/{SERVER}",
        json=payload,
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=90,
    )
    body = r.json()
    text = ((body.get("result") or {}).get("content") or [{}])[0].get("text", "")
    return {
        "request_id": rid,
        "egress": text,
        "raw_ssn": "123-45-6789" in text,
        "masked": "***" in text,
        "blocked": "[BLOCKED]" in text,
    }


def event_for_request(c: httpx.Client, tok: str, request_id: str) -> dict:
    r = c.get(
        f"{CONTROL}/api/mcp-connector/events/?limit=20&server_slug={SERVER}",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    for row in rows:
        md = row.get("metadata") or {}
        if md.get("request_id") == request_id or row.get("request_id") == request_id:
            return row
    return rows[0] if rows else {}


def create_scan_control(c: httpx.Client, tok: str, **kw) -> str:
    body = {
        "scope_type": kw.get("scope_type", "org"),
        "direction": kw["direction"],
        "tier": kw.get("tier", "tier1"),
        "enabled": True,
        "action": kw["action"],
        "priority": kw.get("priority", MARK),
    }
    r = c.post(
        f"{CONTROL}/api/mcp-connector/scan-controls/",
        headers={"Authorization": f"Bearer {tok}"},
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]


def delete_scan_control(c: httpx.Client, tok: str, cid: str) -> None:
    c.delete(
        f"{CONTROL}/api/mcp-connector/scan-controls/{cid}/",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    evidence: dict = {"org": ORG, "server": SERVER, "cases": []}

    with httpx.Client() as c:
        tok = login(c)

        # Baseline: firewall config frameworks (informational)
        fw = c.get(f"{CONTROL}/api/firewall/config/", headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        fw_data = fw.json() if fw.is_success else {}
        evidence["firewall_config"] = {
            "compliance_frameworks": fw_data.get("compliance_frameworks"),
            "mcp_tier2_enabled": fw_data.get("mcp_tier2_enabled"),
        }

        # Baseline scan-control count (do not delete unrelated org controls)
        controls = c.get(
            f"{CONTROL}/api/mcp-connector/scan-controls/",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=30,
        ).json()
        ctrl_list = controls if isinstance(controls, list) else controls.get("results", [])
        evidence["baseline_scan_rows"] = len([r for r in ctrl_list if r.get("priority") != MARK])
        for row in ctrl_list:
            if row.get("priority") == MARK:
                delete_scan_control(c, tok, row["id"])
        bust_scan_cache()
        time.sleep(1.5)

        # Case A: zero scan controls → raw SSN (scan-off gate) when baseline is 0
        rid_a = f"pl-a-{uuid.uuid4().hex[:8]}"
        a = gw_echo(c, "SSN 123-45-6789", req_id=rid_a)
        ev = event_for_request(c, tok, rid_a)
        evidence["cases"].append(
            {
                "name": "scan_controls_zero_raw_ssn",
                "gw": a,
                "decision": ev.get("decision"),
                "tags": ev.get("compliance_tags"),
                "scan_skipped": any(
                    (s or {}).get("scan_stage") == "scan_skipped"
                    for s in (ev.get("metadata") or {}).get("scan_trace") or []
                ),
                "pass": (
                    evidence["baseline_scan_rows"] == 0
                    and a["raw_ssn"]
                    and not a["masked"]
                ),
            }
        )

        # Case B: org tier1 monitor both directions — F-010: monitor must NOT mask (raw egress)
        cid = create_scan_control(c, tok, direction="both", action="monitor")
        bust_scan_cache()
        time.sleep(1.5)
        rid_b = f"pl-b-{uuid.uuid4().hex[:8]}"
        b = gw_echo(c, "SSN 123-45-6789", req_id=rid_b)
        ev = event_for_request(c, tok, rid_b)
        trace = (ev.get("metadata") or {}).get("scan_trace") or []
        evidence["cases"].append(
            {
                "name": "scan_control_monitor_raw_ssn_egress",
                "gw": b,
                "decision": ev.get("decision"),
                "tags": ev.get("compliance_tags"),
                "tier1_actions": [
                    {k: s.get(k) for k in ("scan_stage", "direction", "action", "finding_count")}
                    for s in trace
                    if isinstance(s, dict) and s.get("scan_stage") == "tier1"
                ],
                "note": "F-010: monitor posture + E12 floor honor per-tier monitor — raw SSN egress on gateway",
                "pass": b["raw_ssn"] and not b["masked"],
            }
        )
        delete_scan_control(c, tok, cid)

        # Case C: org tier1 output redact only — masked egress
        cid = create_scan_control(c, tok, direction="output", action="redact")
        bust_scan_cache()
        time.sleep(1.5)
        rid_c = f"pl-c-{uuid.uuid4().hex[:8]}"
        c_out = gw_echo(c, "SSN 123-45-6789", req_id=rid_c)
        ev = event_for_request(c, tok, rid_c)
        evidence["cases"].append(
            {
                "name": "scan_control_output_redact",
                "gw": c_out,
                "decision": ev.get("decision"),
                "pass": c_out["masked"] and not c_out["raw_ssn"],
            }
        )
        delete_scan_control(c, tok, cid)

        # Case D: compliance_frameworks empty does NOT suppress detection tags when scanning
        orig_fw = fw_data.copy()
        patch = {"compliance_frameworks": []}
        c.patch(
            f"{CONTROL}/api/firewall/config/",
            headers={"Authorization": f"Bearer {tok}"},
            json=patch,
            timeout=30,
        )
        cid = create_scan_control(c, tok, direction="both", action="redact")
        bust_scan_cache()
        time.sleep(1.5)
        rid_d = f"pl-d-{uuid.uuid4().hex[:8]}"
        d = gw_echo(c, "SSN 123-45-6789", req_id=rid_d)
        ev = event_for_request(c, tok, rid_d)
        evidence["cases"].append(
            {
                "name": "empty_compliance_frameworks_still_tags_on_scan",
                "gw": d,
                "decision": ev.get("decision"),
                "tags": ev.get("compliance_tags"),
                "frameworks_empty": True,
                "pass": bool(ev.get("compliance_tags")),
            }
        )
        delete_scan_control(c, tok, cid)
        bust_scan_cache()
        if orig_fw:
            c.patch(
                f"{CONTROL}/api/firewall/config/",
                headers={"Authorization": f"Bearer {tok}"},
                json={"compliance_frameworks": orig_fw.get("compliance_frameworks", [])},
                timeout=30,
            )

    evidence["planesPass"] = all(case.get("pass") for case in evidence["cases"])
    OUT.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "planesPass": evidence["planesPass"], "cases": len(evidence["cases"])}, indent=2))
    return 0 if evidence["planesPass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
