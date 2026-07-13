#!/usr/bin/env python3
"""Tier-2 org gate live matrix — tier2 control present but org mcp_tier2_enabled=false.

Writes mcp-parallel/findings/mcp-arch-validation-2026-07-08/tier2-org-gate-live.json
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import httpx

try:
    import redis
except ImportError:
    redis = None

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-arch-validation-2026-07-08/tier2-org-gate-live.json"
CONTROL = "http://127.0.0.1:8100"
GATEWAY = "http://127.0.0.1:8300"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"
ORG = "zeroshield"
SERVER = "everything-1"
MARK = 8891


def bust():
    if not redis:
        return
    r = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
    for k in (f"mcp:scan_ver:{ORG}", f"mcp:scan_ver:{ORG}:{SERVER}"):
        try:
            r.incr(k)
        except redis.ResponseError:
            r.delete(k)
            r.incr(k)


def login(c: httpx.Client) -> str:
    r = c.post(f"{CONTROL}/api/auth/token/", json={"email": EMAIL, "password": PASS}, timeout=30)
    r.raise_for_status()
    return r.json()["access"]


def gw_echo(c: httpx.Client, key: str, req_id: str) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": "SSN 123-45-6789"}},
    }
    r = c.post(
        f"{GATEWAY}/gateway/{ORG}/mcp/{SERVER}",
        json=payload,
        headers={"Authorization": f"Bearer {key}"},
        timeout=90,
    )
    body = r.json()
    text = ((body.get("result") or {}).get("content") or [{}])[0].get("text", "")
    return {"egress": text, "blocked": "[BLOCKED]" in text}


def event_for(c: httpx.Client, tok: str, req_id: str, *, attempts: int = 8, delay_s: float = 0.5) -> dict:
    h = {"Authorization": f"Bearer {tok}"}
    for _ in range(attempts):
        r = c.get(f"{CONTROL}/api/mcp-connector/events/?limit=30&server_slug={SERVER}", headers=h, timeout=30)
        body = r.json()
        rows = body if isinstance(body, list) else body.get("results", [])
        for row in rows:
            md = row.get("metadata") or {}
            if row.get("request_id") == req_id or md.get("request_id") == req_id:
                return row
        time.sleep(delay_s)
    return {}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    mf = json.loads((ROOT / "scripts/ralph/.mcp_scale_manifest.json").read_text())
    key = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)
    evidence: dict = {"cases": []}

    with httpx.Client() as c:
        tok = login(c)
        h = {"Authorization": f"Bearer {tok}"}
        fw = c.get(f"{CONTROL}/api/firewall/config/", headers=h, timeout=30).json()
        orig_tier2 = fw.get("mcp_tier2_enabled")

        # ensure tier1 control exists so scanning runs
        sc = c.get(f"{CONTROL}/api/mcp-connector/scan-controls/", headers=h, timeout=30).json()
        rows = sc if isinstance(sc, list) else sc.get("results", [])
        tier1_id = None
        for row in rows:
            if row.get("priority") == MARK:
                c.delete(f"{CONTROL}/api/mcp-connector/scan-controls/{row['id']}/", headers=h, timeout=30)
        tier1_id = c.post(
            f"{CONTROL}/api/mcp-connector/scan-controls/",
            headers=h,
            json={
                "scope_type": "org",
                "direction": "both",
                "tier": "tier1",
                "enabled": True,
                "action": "monitor",
                "priority": MARK,
            },
            timeout=30,
        ).json()["id"]
        tier2_id = c.post(
            f"{CONTROL}/api/mcp-connector/scan-controls/",
            headers=h,
            json={
                "scope_type": "org",
                "direction": "both",
                "tier": "tier2",
                "enabled": True,
                "action": "block",
                "priority": MARK + 1,
            },
            timeout=30,
        ).json()["id"]
        bust()
        time.sleep(1.5)

        # Case A: tier2 enabled at org — tier2 may run
        c.put(
            f"{CONTROL}/api/firewall/config/",
            headers=h,
            json={"mcp_tier2_enabled": True},
            timeout=30,
        )
        bust()
        time.sleep(1.5)
        rid_a = f"t2a-{uuid.uuid4().hex[:8]}"
        gw_echo(c, key, rid_a)
        ev_a = event_for(c, tok, rid_a)
        trace_a = (ev_a.get("metadata") or {}).get("scan_trace") or []
        has_tier2_a = any((s or {}).get("scan_stage") == "tier2" for s in trace_a)
        evidence["cases"].append({
            "name": "tier2_enabled",
            "has_tier2_stage": has_tier2_a,
            "trace_stages": [s.get("scan_stage") for s in trace_a if isinstance(s, dict)],
        })

        # Case B: tier2 disabled at org — tier2_skipped
        c.put(
            f"{CONTROL}/api/firewall/config/",
            headers=h,
            json={"mcp_tier2_enabled": False},
            timeout=30,
        )
        bust()
        time.sleep(1.5)
        rid_b = f"t2b-{uuid.uuid4().hex[:8]}"
        gw_echo(c, key, rid_b)
        ev_b = event_for(c, tok, rid_b)
        trace_b = (ev_b.get("metadata") or {}).get("scan_trace") or []
        skipped = [s for s in trace_b if isinstance(s, dict) and s.get("scan_stage") == "tier2_skipped"]
        evidence["cases"].append({
            "name": "tier2_disabled_org",
            "tier2_skipped": bool(skipped),
            "reason": (skipped[0] or {}).get("reason") if skipped else None,
            "has_tier2_stage": any((s or {}).get("scan_stage") == "tier2" for s in trace_b),
            "trace_stages": [s.get("scan_stage") for s in trace_b if isinstance(s, dict)],
            "pass": bool(skipped) and any(
                s.get("reason") == "org_mcp_tier2_disabled" for s in skipped if isinstance(s, dict)
            ),
        })

        # cleanup
        c.delete(f"{CONTROL}/api/mcp-connector/scan-controls/{tier1_id}/", headers=h, timeout=30)
        c.delete(f"{CONTROL}/api/mcp-connector/scan-controls/{tier2_id}/", headers=h, timeout=30)
        c.put(
            f"{CONTROL}/api/firewall/config/",
            headers=h,
            json={"mcp_tier2_enabled": orig_tier2},
            timeout=30,
        )
        bust()

    evidence["tier2GatePass"] = evidence["cases"][-1].get("pass") is True
    OUT.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"tier2GatePass": evidence["tier2GatePass"]}, indent=2))
    return 0 if evidence["tier2GatePass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
