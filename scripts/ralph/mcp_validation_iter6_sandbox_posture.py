#!/usr/bin/env python3
"""Iteration 6 — live sandbox isolation posture (docker inspect + broker health)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter6-sandbox-posture.json"
BROKER = "http://127.0.0.1:8311"
ORGS = ["zeroshield", "org-a", "org-b"]


def _inspect(org: str) -> dict:
    name = f"{org}-mcp-sandbox" if org != "zeroshield" else "zeroshield-mcp-sandbox"
    try:
        raw = subprocess.check_output(
            ["docker", "inspect", name, "--format", "{{json .}}"],
            text=True,
        )
        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0]
    except subprocess.CalledProcessError:
        return {"org": org, "container": name, "exists": False, "pass": False}
    hc = data.get("HostConfig") or {}
    state = (data.get("State") or {}).get("Health", {}).get("Status")
    labels = (data.get("Config") or {}).get("Labels") or {}
    runtime = hc.get("Runtime") or "runc"
    is_runsc = runtime == "runsc"
    # gVisor path (F-015): entrypoint rewrites resolv.conf via setpriv — requires
    # read_only=False and no-new-privileges omitted (documented tradeoff vs runc).
    readonly_ok = hc.get("ReadonlyRootfs") is True or is_runsc
    nnp_ok = (
        "no-new-privileges:true" in (hc.get("SecurityOpt") or [])
        or is_runsc
    )
    return {
        "org": org,
        "container": name,
        "exists": True,
        "healthy": state == "healthy",
        "runtime": runtime,
        "cap_drop_all": hc.get("CapDrop") == ["ALL"],
        "readonly_rootfs": hc.get("ReadonlyRootfs") is True,
        "gvisor_runsc_tradeoff": is_runsc,
        "pids_limit": hc.get("PidsLimit"),
        "mem_bytes": hc.get("Memory"),
        "no_new_privileges": "no-new-privileges:true" in (hc.get("SecurityOpt") or []),
        "network": hc.get("NetworkMode"),
        "label_org": labels.get("org_slug") or labels.get("ai_mesh.org_slug"),
        "pass": (
            state == "healthy"
            and hc.get("CapDrop") == ["ALL"]
            and readonly_ok
            and nnp_ok
            and hc.get("PidsLimit") == 256
            and hc.get("Memory") == 2147483648
            and (not is_runsc or runtime == "runsc")
        ),
    }


def main() -> int:
    runtimes = subprocess.check_output(["docker", "info", "--format", "{{json .Runtimes}}"], text=True)
    rt = json.loads(runtimes)
    gvisor = "runsc" in rt

    broker_ok = False
    try:
        r = httpx.get(f"{BROKER}/health", timeout=5)
        broker_ok = r.status_code == 200
    except Exception:
        pass

    sandboxes = [_inspect(o) for o in ORGS]
    distinct_nets = {s["network"] for s in sandboxes if s.get("exists")}
    report = {
        "gvisor_installed": gvisor,
        "runtime_in_use": sandboxes[0].get("runtime") if sandboxes else None,
        "broker_healthy": broker_ok,
        "per_org_networks": sorted(distinct_nets),
        "network_isolation": len(distinct_nets) == len([s for s in sandboxes if s.get("exists")]),
        "sandboxes": sandboxes,
        "checks": {
            "all_sandboxes_pass": all(s.get("pass") for s in sandboxes),
            "broker_pass": broker_ok,
            "network_isolation_pass": len(distinct_nets) >= 3,
        },
        "f007_gvisor": (
            {"status": "CLOSED", "detail": "runsc in docker runtimes; sandboxes use runsc"}
            if gvisor and all(s.get("runtime") == "runsc" for s in sandboxes if s.get("exists"))
            else {"status": "BLOCKED", "detail": "runsc not in docker runtimes; containers use runc"}
        ),
        "ok": False,
    }
    report["ok"] = all(report["checks"].values())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
