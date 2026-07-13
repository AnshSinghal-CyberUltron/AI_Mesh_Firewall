#!/usr/bin/env python3
"""Live scan-control matrix validation — gateway JSON-RPC path.

Proves scan_controls_configured gate + direction/action/scope effects.
Writes evidence JSON under mcp-parallel/findings/mcp-arch-validation-2026-07-08/.
"""
from __future__ import annotations

import json
import os
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
OUT = ROOT / "mcp-parallel/findings/mcp-arch-validation-2026-07-08/scan_matrix_live.json"
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
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"_raw": r.text[:500]}
    result = body.get("result") or {}
    content = result.get("content") or []
    text = content[0].get("text", "") if content else ""
    err = body.get("error")
    blocked = "[BLOCKED]" in text or bool(err)
    return {
        "status": r.status_code,
        "latency_ms": ms,
        "egress_text": text,
        "blocked": blocked,
        "raw_ssn_visible": "123-45-6789" in text,
        "masked": "***" in text or "REDACTED" in text,
        "error": err,
    }


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
    r = client.get(
        f"{CONTROL}/api/mcp-connector/scan-controls/",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    for row in rows:
        if row.get("priority") == MARK:
            _delete_control(client, tok, row["id"])


def _server_id(client: httpx.Client, tok: str) -> str:
    r = client.get(
        f"{CONTROL}/api/mcp-connector/servers/",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    for row in rows:
        if row.get("server_slug") == SERVER:
            return row["id"]
    raise RuntimeError(f"server {SERVER} not found")


def main() -> int:
    mf = json.loads(MANIFEST.read_text())
    key = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)
    results: dict = {"org": ORG, "server": SERVER, "cases": [], "checks": {}}
    created: list[str] = []

    with httpx.Client() as client:
        tok = _login(client)
        _delete_all_marked(client, tok)
        server_id = _server_id(client, tok)

        def run_case(name: str, expect: dict) -> None:
            time.sleep(1.5)  # cache version bump settle
            got = _gw_echo(client, key)
            row = {"case": name, "expect": expect, "got": got}
            row["pass"] = all(
                got.get(k) == v for k, v in expect.items() if k in got
            )
            results["cases"].append(row)
            print(f"{name}: pass={row['pass']} egress={got.get('egress_text','')[:60]}")

        # Baseline: 0 controls
        sc = client.get(
            f"{CONTROL}/api/mcp-connector/scan-controls/",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=30,
        )
        rows = sc.json() if isinstance(sc.json(), list) else sc.json().get("results", [])
        results["baseline_scan_rows"] = len([r for r in rows if r.get("priority") != MARK])
        run_case(
            "baseline_zero_controls",
            {"raw_ssn_visible": True, "masked": False, "blocked": False},
        )

        # Org input+output monitor — scan ON, tag posture (mask may still happen on output floor)
        cid = _create_control(
            client, tok, scope_type="org", direction="both", action="monitor", tier="tier1"
        )
        created.append(cid)
        run_case(
            "org_tier1_monitor",
            {"blocked": False},  # monitor must not hard-block
        )

        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1.5)

        # Org input redact
        cid = _create_control(
            client, tok, scope_type="org", direction="input", action="redact", tier="tier1"
        )
        created.append(cid)
        run_case("org_tier1_input_redact", {"masked": True, "blocked": False})

        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1.5)

        # Org output redact (echo returns SSN in result text)
        cid = _create_control(
            client, tok, scope_type="org", direction="output", action="redact", tier="tier1"
        )
        created.append(cid)
        run_case("org_tier1_output_redact", {"masked": True, "blocked": False})

        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1.5)

        # Server-scoped block on input
        cid = _create_control(
            client,
            tok,
            scope_type="server",
            server_id=server_id,
            direction="input",
            action="block",
            tier="tier1",
        )
        created.append(cid)
        run_case("server_tier1_input_block", {"blocked": True})

        _delete_control(client, tok, cid)
        created.remove(cid)
        time.sleep(1.5)

        # Precedence: org redact + server block → server wins for input
        cid_org = _create_control(
            client, tok, scope_type="org", direction="input", action="redact", tier="tier1"
        )
        cid_srv = _create_control(
            client,
            tok,
            scope_type="server",
            server_id=server_id,
            direction="input",
            action="block",
            tier="tier1",
            priority=MARK + 1,
        )
        created.extend([cid_org, cid_srv])
        run_case("precedence_server_block_over_org_redact", {"blocked": True})
        _delete_control(client, tok, cid_srv)
        _delete_control(client, tok, cid_org)
        created.clear()
        time.sleep(1.5)

        run_case(
            "post_cleanup_zero_controls",
            {"raw_ssn_visible": True, "masked": False, "blocked": False},
        )

        _delete_all_marked(client, tok)

    results["checks"] = {c["case"]: c["pass"] for c in results["cases"]}
    results["matrixPass"] = all(results["checks"].values())
    results["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    print("Wrote", OUT)
    print("matrixPass:", results["matrixPass"])
    return 0 if results["matrixPass"] else 1


if __name__ == "__main__":
    sys.exit(main())
