#!/usr/bin/env python3
"""Capture zeroshield MCP enforcement configuration snapshot for audit evidence."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import redis

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
OUT = Path(
    os.environ.get(
        "OUT_DIR",
        os.path.join(ROOT, "mcp-parallel", "findings", "mcp-enforcement-audit-2026-07-07"),
    )
)
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
ORG = os.environ.get("ORG", "zeroshield")
EMAIL = os.environ.get("AUDIT_EMAIL", "admin@zeroshield.io")
PASS = os.environ.get("AUDIT_PASS", "Adm1n!Pass#2024")


def main() -> int:
    tok = httpx.post(
        f"{CONTROL}/api/auth/token/",
        json={"email": EMAIL, "password": PASS},
        timeout=30.0,
    ).json()["access"]
    headers = {"Authorization": f"Bearer {tok}"}

    snap: dict = {"org": ORG}

    # scan controls
    r = httpx.get(f"{CONTROL}/api/mcp-connector/scan-controls/", headers=headers, timeout=30.0)
    rows = r.json() if r.status_code == 200 else []
    if isinstance(rows, dict):
        rows = rows.get("results") or rows.get("scan_controls") or []
    snap["scan_control_rows"] = len(rows)
    snap["scan_controls"] = rows[:20]

    # servers summary
    r = httpx.get(f"{CONTROL}/api/mcp-connector/servers/", headers=headers, timeout=30.0)
    servers = r.json()
    if isinstance(servers, dict):
        servers = servers.get("results") or []
    snap["servers"] = [
        {
            "slug": s.get("server_slug"),
            "default_scan_action": s.get("default_scan_action"),
            "connection_status": s.get("connection_status"),
            "tools_count": s.get("tools_count"),
        }
        for s in servers
    ]

    # MCP policies
    r = httpx.get(
        f"{CONTROL}/api/policies/?policy_domain=mcp",
        headers=headers,
        timeout=60.0,
    )
    policies = r.json()
    if isinstance(policies, dict):
        policies = policies.get("results") or []
    snap["mcp_policy_count"] = len(policies)
    snap["mcp_policies"] = [
        {"code": p.get("code"), "name": p.get("name"), "enabled": p.get("enabled")}
        for p in policies[:30]
    ]

    # org firewall config (compliance frameworks)
    r = httpx.get(f"{CONTROL}/api/firewall/config/", headers=headers, timeout=30.0)
    if r.status_code == 200:
        cfg = r.json()
        snap["compliance_frameworks"] = cfg.get("compliance_frameworks")
        snap["mcp_tier2_enabled"] = cfg.get("mcp_tier2_enabled")

    # Redis policy bundle version
    try:
        rc = redis.Redis(host=os.environ.get("REDIS_HOST", "127.0.0.1"), port=6379, db=0)
        snap["policy_bundle_version"] = rc.get(f"policies:version:{ORG}")
        if isinstance(snap["policy_bundle_version"], bytes):
            snap["policy_bundle_version"] = snap["policy_bundle_version"].decode()
    except Exception as exc:
        snap["policy_bundle_version_error"] = str(exc)

    snap["gateway_env"] = {
        "GATEWAY_MCP_REDACT_RESULT_ON_DETECT": os.environ.get("GATEWAY_MCP_REDACT_RESULT_ON_DETECT", "(default true)"),
        "GATEWAY_MCP_BLOCK_ON_CREDENTIAL": os.environ.get("GATEWAY_MCP_BLOCK_ON_CREDENTIAL", "(default true)"),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "state_snapshot.json"
    path.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"wrote": str(path), "scan_rows": snap["scan_control_rows"], "policies": snap["mcp_policy_count"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
