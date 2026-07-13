#!/usr/bin/env python3
"""Iteration 12 — F-012 ext-floor fix, image bake gate, full regression closeout.

Runs prior iteration gates + documents F-012 (static hardening floors vs explicit monitor).

Writes: mcp-parallel/findings/mcp-validation/iter12-closeout.json
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter12-closeout.json"
GW = ROOT / "gateway"


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 600) -> dict:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-800:],
        "stderr_tail": (proc.stderr or "")[-400:],
    }


def _load_iter11() -> dict:
    spec = importlib.util.spec_from_file_location(
        "iter11", ROOT / "scripts/ralph/mcp_validation_iter11_combinatorial.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    rc = mod.main()
    data = json.loads((ROOT / "mcp-parallel/findings/mcp-validation/iter11-combinatorial.json").read_text())
    return {"exit_code": rc, "report": data}


def _load_iter10() -> dict:
    proc = subprocess.run(
        [str(GW / ".venv/bin/python"), str(ROOT / "scripts/ralph/mcp_validation_iter10_policy_ext_sdk_live.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    data = json.loads((ROOT / "mcp-parallel/findings/mcp-validation/iter10-policy-ext-sdk-live.json").read_text())
    return {"exit_code": proc.returncode, "report": data}


def _gvisor_posture() -> dict:
    proc = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=30)
    runtimes = ""
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            if "Runtimes:" in line:
                runtimes = line.strip()
    has_runsc = "runsc" in runtimes
    return {
        "runtimes_line": runtimes,
        "runsc_installed": has_runsc,
        "pass": has_runsc,
        "infra_blocker": not has_runsc,
        "finding": "F-007",
    }


def main() -> int:
    report: dict = {
        "f012": {
            "title": "ext_mcp_proxy static floors vs explicit org monitor",
            "fix": "_explicit_monitor_posture + _static_hardening_floors_enabled in mcp_proxy.py",
            "root_cause": "F-010 treated default tag scan_action as observe-only on ext path (enabled_info=None)",
        },
        "gates": {},
    }

    report["gates"]["bare_proxy_full"] = _run(
        [str(GW / ".venv/bin/python"), "-m", "pytest", "ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py", "-q"],
        cwd=GW,
    )
    report["gates"]["scan_off_by_default"] = _run(
        [str(GW / ".venv/bin/python"), "-m", "pytest", "ai_mesh_gateway/tests/test_mcp_scan_off_by_default.py", "-q"],
        cwd=GW,
    )
    report["gates"]["iter11"] = _load_iter11()
    report["gates"]["iter10"] = _load_iter10()
    report["gates"]["gvisor"] = _gvisor_posture()

    report["checks"] = {
        "bare_proxy_full_pass": report["gates"]["bare_proxy_full"]["ok"],
        "scan_off_by_default_pass": report["gates"]["scan_off_by_default"]["ok"],
        "iter11_pass": report["gates"]["iter11"]["exit_code"] == 0,
        "iter10_pass": report["gates"]["iter10"]["exit_code"] == 0,
        "image_parity_pass": report["gates"]["iter11"]["report"].get("checks", {}).get("image_parity_pass"),
        "gvisor_pass": report["gates"]["gvisor"]["pass"],
    }
    report["ok"] = all(
        report["checks"].get(k)
        for k in (
            "bare_proxy_full_pass",
            "scan_off_by_default_pass",
            "iter11_pass",
            "iter10_pass",
            "image_parity_pass",
        )
    )
    report["infra_blockers"] = []
    if not report["checks"]["gvisor_pass"]:
        report["infra_blockers"].append("F-007 gVisor (runsc) not installed — sandboxes use runc")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps({"ok": report["ok"], "checks": report["checks"], "infra_blockers": report["infra_blockers"]}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
