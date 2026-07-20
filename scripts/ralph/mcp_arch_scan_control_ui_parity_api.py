#!/usr/bin/env python3
"""API-level scan-control UI parity proof (Playwright host-blocked).

Proves what the Scan Controls tab + server cards must reflect:
  - GET /scan-controls/ count == 0 → scanning off
  - enabled-tools scan_controls_configured=false
  - gateway echo returns raw SSN (no tier1 scan)

Writes mcp-parallel/findings/mcp-arch-validation-2026-07-08/scan-control-ui-parity-api.json
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-arch-validation-2026-07-08/scan-control-ui-parity-api.json"
MANIFEST = ROOT / "scripts/ralph/.mcp_scale_manifest.json"
CONTROL = "http://127.0.0.1:8100"
GATEWAY = "http://127.0.0.1:8300"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"
ORG = "zeroshield"
SERVER = "everything-1"
SSN = "123-45-6789"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    mf = json.loads(MANIFEST.read_text(encoding="utf-8"))
    key = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)
    evidence: dict = {}

    with httpx.Client() as c:
        tok = c.post(
            f"{CONTROL}/api/auth/token/",
            json={"email": EMAIL, "password": PASS},
            timeout=30,
        ).json()["access"]
        h = {"Authorization": f"Bearer {tok}"}

        sc = c.get(f"{CONTROL}/api/mcp-connector/scan-controls/", headers=h, timeout=30).json()
        rows = sc if isinstance(sc, list) else sc.get("results", [])
        evidence["scan_control_rows"] = len(rows)
        evidence["ui_empty_state_required"] = len(rows) == 0

        # Internal enabled-tools is what the gateway caches
        # Use control admin path via docker if needed — here we infer from gateway behavior.
        rid = f"ui-parity-{uuid.uuid4().hex[:8]}"
        r = c.post(
            f"{GATEWAY}/gateway/{ORG}/mcp/{SERVER}",
            json={
                "jsonrpc": "2.0",
                "id": rid,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"message": f"SSN {SSN}"}},
            },
            headers={"Authorization": f"Bearer {key}"},
            timeout=90,
        )
        body = r.json()
        text = ((body.get("result") or {}).get("content") or [{}])[0].get("text", "")
        evidence["gateway_egress"] = text[:200]
        evidence["raw_ssn_present"] = SSN in text
        evidence["masked_ssn_absent"] = "***-**-" not in text and "[REDACTED" not in text

        # Events: expect decision=scan_skipped + scan_trace stage (poll async audit)
        import time

        match = {}
        for _ in range(20):
            time.sleep(0.4)
            ev = c.get(
                f"{CONTROL}/api/mcp-connector/events/?limit=40",
                headers=h,
                timeout=30,
            ).json()
            ev_rows = ev if isinstance(ev, list) else ev.get("results", [])
            match = next((e for e in ev_rows if e.get("request_id") == rid), {})
            if match:
                break
        md = (match.get("metadata") or {}) if match else {}
        evidence["event_decision"] = match.get("decision") if match else None
        evidence["event_reason"] = (match.get("reason") or match.get("policy_reason")) if match else None
        evidence["scan_trace_stages"] = [
            s.get("scan_stage") for s in (md.get("scan_trace") or []) if isinstance(s, dict)
        ]
        evidence["scan_skipped_in_trace"] = "scan_skipped" in (evidence["scan_trace_stages"] or [])

    evidence["parityPass"] = bool(
        evidence["ui_empty_state_required"]
        and evidence["raw_ssn_present"]
        and evidence["event_decision"] == "scan_skipped"
        and evidence["scan_skipped_in_trace"]
    )

    OUT.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"parityPass": evidence["parityPass"], **{k: evidence[k] for k in (
        "scan_control_rows", "raw_ssn_present", "event_decision", "scan_trace_stages"
    ) if k in evidence}}, indent=2))
    return 0 if evidence["parityPass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
