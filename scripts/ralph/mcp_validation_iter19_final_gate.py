#!/usr/bin/env python3
"""Iteration 19 — final MCP platform validation gate.

Runs:
  1. iter18 SSE closeout (parity + 8/8 SSE + iter14 regression)
  2. Full transport fleet (strict 16/16 with SSE warmup/retry)
  3. gVisor / runtime infra proof (F-007 documented blocker)
  4. Sandbox posture snapshot
  5. UI gates (iter4 + iter5 Playwright)
  6. Core pytest unit locks

Writes: mcp-parallel/findings/mcp-validation/iter19-final-gate.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter19-final-gate.json"
PY = ROOT / "gateway/.venv/bin/python"
NODE = os.environ.get("NODE_BIN", "node")


def _run(cmd: list[str], *, timeout: int = 3600, env: dict | None = None) -> dict:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "cmd": cmd,
        "exit_code": proc.returncode,
        "pass": proc.returncode == 0,
        "tail": (proc.stdout + proc.stderr)[-3000:],
    }


def _gvisor_infra() -> dict:
    runsc = subprocess.run(["which", "runsc"], capture_output=True, text=True)
    info = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"], capture_output=True, text=True)
    runtimes = {}
    if info.returncode == 0 and info.stdout.strip():
        try:
            runtimes = json.loads(info.stdout.strip())
        except json.JSONDecodeError:
            runtimes = {"raw": info.stdout.strip()[:500]}
    sandbox = subprocess.run(
        [
            "docker",
            "inspect",
            "zeroshield-mcp-sandbox",
            "--format",
            "{{.HostConfig.Runtime}}",
        ],
        capture_output=True,
        text=True,
    )
    runtime = sandbox.stdout.strip() if sandbox.returncode == 0 else None
    return {
        "runsc_installed": runsc.returncode == 0,
        "runsc_path": runsc.stdout.strip() or None,
        "docker_runtimes": runtimes,
        "sandbox_runtime": runtime,
        "f007_gvisor": "BLOCKED" if not (runsc.returncode == 0 and "runsc" in runtimes) else "AVAILABLE",
        "pass": True,  # infra documented — not a code gate failure
    }


def _sandbox_posture() -> dict:
    proc = subprocess.run(
        [
            "docker",
            "inspect",
            "zeroshield-mcp-sandbox",
            "--format",
            "{{json .HostConfig}}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return {"pass": False, "error": proc.stderr.strip()[:500]}
    hc = json.loads(proc.stdout)
    caps = hc.get("CapDrop") or []
    return {
        "pass": "ALL" in caps,
        "cap_drop_all": "ALL" in caps,
        "readonly_rootfs": hc.get("ReadonlyRootfs"),
        "memory": hc.get("Memory"),
        "pids_limit": hc.get("PidsLimit"),
        "no_new_privileges": hc.get("SecurityOpt"),
    }


def _run_ui(script: str, *, env: dict, attempts: int = 3) -> dict:
    """Run a Playwright UI gate with short retries (console/API flakes under load)."""
    last: dict = {}
    for i in range(attempts):
        last = _run([NODE, str(ROOT / f"scripts/ralph/{script}")], timeout=300, env=env)
        if last.get("pass"):
            last["attempt"] = i + 1
            return last
        time.sleep(5)
    last["attempts"] = attempts
    return last


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    report: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "iteration": 19, "gates": {}}

    report["gates"]["iter18_closeout"] = _run([str(PY), str(ROOT / "scripts/ralph/mcp_validation_iter18_sse_closeout.py")])
    report["gates"]["iter18_data"] = _load_json(ROOT / "mcp-parallel/findings/mcp-validation/iter18-sse-closeout.json")

    # Login throttle (5/min per email) — pause before the next harness that re-authenticates.
    time.sleep(15)

    report["gates"]["transport_fleet"] = _run(
        [str(PY), str(ROOT / "scripts/ralph/mcp_arch_fleet_execution_live.py")],
        timeout=7200,
    )
    fleet = _load_json(ROOT / "mcp-parallel/findings/mcp-arch-validation-2026-07-08/fleet-tool-execution.json")
    report["fleet"] = {
        "strictFleetPass": fleet.get("strictFleetPass"),
        "ok_count": fleet.get("ok_count"),
        "connected_count": fleet.get("connected_count"),
        "failures": [s for s in fleet.get("servers", []) if not s.get("ok")],
    }

    report["gates"]["gvisor_infra"] = _gvisor_infra()
    report["gates"]["sandbox_posture"] = _sandbox_posture()

    node_path = f"NODE_PATH={ROOT}/tests/e2e/node_modules"
    ui_env = {**os.environ, "NODE_PATH": str(ROOT / "tests/e2e/node_modules"), "BASE_URL": "http://127.0.0.1:8180"}
    report["gates"]["ui_iter4"] = _run_ui("mcp_validation_iter4_ui.mjs", env=ui_env)
    report["gates"]["ui_iter5"] = _run_ui("mcp_validation_iter5_scan_ui.mjs", env=ui_env)

    report["gates"]["pytest_scan_off"] = _run(
        [str(PY), "-m", "pytest", "gateway/ai_mesh_gateway/tests/test_mcp_scan_off_by_default.py", "-q"],
        timeout=600,
    )
    report["gates"]["pytest_ext_proxy"] = _run(
        [str(PY), "-m", "pytest", "gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py", "-q"],
        timeout=900,
    )

    code_gates = [
        report["gates"]["iter18_closeout"]["pass"],
        report["gates"]["transport_fleet"]["pass"],
        fleet.get("strictFleetPass") is True,
        report["gates"]["sandbox_posture"].get("pass"),
        report["gates"]["ui_iter4"]["pass"],
        report["gates"]["ui_iter5"]["pass"],
        report["gates"]["pytest_scan_off"]["pass"],
        report["gates"]["pytest_ext_proxy"]["pass"],
    ]
    report["code_validation_pass"] = all(code_gates)
    report["infra_blockers"] = []
    if report["gates"]["gvisor_infra"]["f007_gvisor"] == "BLOCKED":
        report["infra_blockers"].append("F-007 gVisor (runsc) not installed — sandboxes use runc")

    # Platform is code-validated when all executable gates pass; gVisor is documented infra residual.
    report["ok"] = report["code_validation_pass"]
    report["production_complete"] = report["ok"] and not report["infra_blockers"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "production_complete": report["production_complete"], "out": str(OUT)}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
