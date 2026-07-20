#!/usr/bin/env python3
"""Iteration 17 — image bake gate, transport fleet, full regression.

1. Verify workspace↔container SHA parity for fix files (pre-bake baseline).
2. Optionally build+recreate control+gateway (IMAGE_BAKE=1).
3. Live transport fleet execution (all connected servers × transport types).
4. Run iter14 combined regression gate.

Writes: mcp-parallel/findings/mcp-validation/iter17-image-bake-transport.json
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter17-image-bake-transport.json"
GW = ROOT / "gateway"

PARITY_FILES = (
    ("control_evaluation_views", "ai_mesh_firewall-control-1",
     ROOT / "control/ai_mesh_control/policy/evaluation_views.py",
     "/app/control/ai_mesh_control/policy/evaluation_views.py"),
    ("control_scan_controls", "ai_mesh_firewall-control-1",
     ROOT / "control/ai_mesh_control/mcp_connector/scan_controls.py",
     "/app/control/ai_mesh_control/mcp_connector/scan_controls.py"),
    ("gateway_mcp_proxy", "ai_mesh_firewall-gateway-1",
     ROOT / "gateway/ai_mesh_gateway/mcp_proxy.py",
     "/app/gateway/ai_mesh_gateway/mcp_proxy.py"),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _container_sha(container: str, inner: str) -> str | None:
    proc = subprocess.run(
        ["docker", "exec", container, "sha256sum", inner],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.split()[0] if proc.stdout.strip() else None


def _parity_report() -> dict:
    rows = {}
    for label, cname, local, remote in PARITY_FILES:
        local_sha = _sha256(local)
        remote_sha = _container_sha(cname, remote)
        rows[label] = {
            "workspace": local_sha,
            "container": remote_sha,
            "match": local_sha == remote_sha and remote_sha is not None,
        }
    rows["pass"] = all(r["match"] for r in rows.values() if isinstance(r, dict) and "match" in r)
    return rows


def _image_bake() -> dict:
    if os.environ.get("IMAGE_BAKE", "1") != "1":
        return {"skipped": True, "reason": "IMAGE_BAKE!=1"}
    build = subprocess.run(
        ["docker", "compose", "build", "control", "gateway"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    up = subprocess.run(
        ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "control", "gateway"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    healthy = False
    for _ in range(60):
        proc = subprocess.run(
            ["docker", "ps", "--filter", "name=control", "--filter", "name=gateway", "--format", "{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if len(lines) >= 2 and all("healthy" in ln.lower() for ln in lines[:2]):
            healthy = True
            break
        time.sleep(3)
    return {
        "build_ok": build.returncode == 0,
        "up_ok": up.returncode == 0,
        "healthy": healthy,
        "build_tail": (build.stdout or "")[-600:],
        "up_tail": (up.stdout or "")[-400:],
        "pass": build.returncode == 0 and up.returncode == 0 and healthy,
    }


def _run_script(rel: str) -> dict:
    proc = subprocess.run(
        [str(GW / ".venv/bin/python"), str(ROOT / rel)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    return {
        "script": rel,
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "tail": (proc.stdout or "")[-800:],
    }


def _gvisor() -> dict:
    proc = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"], capture_output=True, text=True, timeout=20)
    rt = json.loads(proc.stdout) if proc.returncode == 0 else {}
    has_runsc = "runsc" in rt
    return {"runsc_installed": has_runsc, "infra_blocker": not has_runsc, "finding": "F-007"}


def main() -> int:
    report: dict = {
        "parity_pre": _parity_report(),
        "image_bake": {},
        "parity_post": {},
        "transport_fleet": {},
        "iter14_combined": {},
        "gvisor": _gvisor(),
        "checks": {},
        "ok": False,
    }

    report["image_bake"] = _image_bake()
    time.sleep(5)
    report["parity_post"] = _parity_report()

    report["transport_fleet"] = _run_script("scripts/ralph/mcp_arch_fleet_execution_live.py")
    report["iter14_combined"] = _run_script("scripts/ralph/mcp_validation_iter14_combined.py")

    report["checks"] = {
        "image_bake_pass": report["image_bake"].get("pass") or report["image_bake"].get("skipped"),
        "parity_post_pass": report["parity_post"].get("pass"),
        "transport_fleet_pass": report["transport_fleet"].get("ok"),
        "iter14_pass": report["iter14_combined"].get("ok"),
    }
    # gVisor is documented infra blocker — not part of code ok gate
    report["ok"] = all(report["checks"].values())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps({"ok": report["ok"], "checks": report["checks"], "gvisor": report["gvisor"]}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
