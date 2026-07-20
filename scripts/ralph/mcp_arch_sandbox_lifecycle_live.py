#!/usr/bin/env python3
"""Live sandbox lifecycle / isolation posture drill for zeroshield org.

Writes mcp-parallel/findings/mcp-arch-validation-2026-07-08/sandbox-lifecycle-live.json
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-arch-validation-2026-07-08/sandbox-lifecycle-live.json"
ORG = "zeroshield"
CONTAINER = f"{ORG}-mcp-sandbox"


def sh(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    report: dict = {"org": ORG, "container": CONTAINER, "checks": {}}

    try:
        insp = json.loads(sh(["docker", "inspect", CONTAINER]))
        cfg = insp[0]
        host = cfg.get("HostConfig") or {}
        state = cfg.get("State") or {}
        labels = (cfg.get("Config") or {}).get("Labels") or {}

        report["running"] = state.get("Running")
        report["oom_killed"] = state.get("OOMKilled")
        report["checks"]["cap_drop_all"] = "ALL" in (host.get("CapDrop") or [])
        report["checks"]["no_new_privileges"] = bool(
            (host.get("SecurityOpt") or []) and any("no-new-privileges" in x for x in host.get("SecurityOpt"))
        )
        report["checks"]["readonly_rootfs"] = bool(host.get("ReadonlyRootfs"))
        report["checks"]["mem_limit_mb"] = round((host.get("Memory") or 0) / (1024 * 1024))
        report["checks"]["pids_limit"] = host.get("PidsLimit")
        report["checks"]["cpu_quota"] = host.get("CpuQuota")
        report["checks"]["label_org_match"] = (
            labels.get("ai_mesh.org_slug") == ORG or labels.get("org_slug") == ORG
        )
        report["checks"]["label_role"] = labels.get("ai_mesh.role") or labels.get("role")

        nets = list((cfg.get("NetworkSettings") or {}).get("Networks") or {})
        report["networks"] = nets
        report["checks"]["per_org_network"] = any(ORG.replace("_", "-") in n or ORG in n for n in nets)

        # runtime
        runtimes = json.loads(sh(["docker", "info", "--format", "{{json .Runtimes}}"]))
        report["runtimes"] = list(runtimes.keys())
        report["checks"]["gvisor_available"] = "runsc" in runtimes

        # recovery: broker ensure endpoint
        try:
            broker_health = sh(["curl", "-sf", "http://127.0.0.1:8311/health"]).strip()
            report["broker_health"] = broker_health
        except Exception as e:
            report["broker_health_error"] = str(e)

    except Exception as exc:
        report["error"] = str(exc)

    report["lifecyclePass"] = all(
        report["checks"].get(k)
        for k in (
            "cap_drop_all",
            "no_new_privileges",
            "readonly_rootfs",
            "label_org_match",
            "per_org_network",
        )
        if report.get("checks")
    ) and report.get("running") is True

    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"lifecyclePass": report.get("lifecyclePass"), "running": report.get("running")}, indent=2))
    return 0 if report.get("lifecyclePass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
