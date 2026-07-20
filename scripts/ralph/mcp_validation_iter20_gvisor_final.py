#!/usr/bin/env python3
"""Iteration 20 — gVisor enablement proof + full platform gate.

1. F-007 gVisor: verify runsc installed + all org sandboxes runtime=runsc
2. Post-gVisor tool smoke (echo via gateway)
3. Re-run iter19 final gate (fleet 16/16, UI, pytest, regression)

Writes: mcp-parallel/findings/mcp-validation/iter20-gvisor-final.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter20-gvisor-final.json"
MANIFEST = ROOT / "scripts/ralph/.mcp_scale_manifest.json"
PY = ROOT / "gateway/.venv/bin/python"
ORGS = ("zeroshield", "org-a", "org-b")


def _gvisor_proof() -> dict:
    runsc = subprocess.run(["which", "runsc"], capture_output=True, text=True)
    info = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"], capture_output=True, text=True)
    runtimes = json.loads(info.stdout.strip()) if info.returncode == 0 else {}
    sandboxes = {}
    all_runsc = True
    for org in ORGS:
        name = f"{org}-mcp-sandbox"
        proc = subprocess.run(
            ["docker", "inspect", name, "--format", "{{.HostConfig.Runtime}}"],
            capture_output=True,
            text=True,
        )
        rt = proc.stdout.strip() if proc.returncode == 0 else "MISSING"
        sandboxes[org] = rt
        if rt != "runsc":
            all_runsc = False
    broker_env = subprocess.run(
        ["docker", "inspect", "ai_mesh_mcp_broker", "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
        capture_output=True,
        text=True,
    )
    broker_runtime = None
    for line in (broker_env.stdout or "").splitlines():
        if line.startswith("MCP_SANDBOX_RUNTIME="):
            broker_runtime = line.split("=", 1)[1]
    return {
        "runsc_installed": runsc.returncode == 0,
        "docker_has_runsc": "runsc" in runtimes,
        "broker_mcp_sandbox_runtime": broker_runtime,
        "sandbox_runtimes": sandboxes,
        "all_sandboxes_runsc": all_runsc,
        "f007_closed": runsc.returncode == 0 and "runsc" in runtimes and all_runsc and broker_runtime == "runsc",
        "pass": runsc.returncode == 0 and "runsc" in runtimes and all_runsc,
    }


def _gvisor_smoke() -> dict:
    data = json.loads(MANIFEST.read_text())
    key = next(o["gateway_key"] for o in data["orgs"] if o["slug"] == "zeroshield")
    t0 = time.perf_counter()
    r = httpx.post(
        "http://127.0.0.1:8300/gateway/zeroshield/mcp/everything-1",
        json={
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "iter20-gvisor-smoke"}},
        },
        headers={"Authorization": f"Bearer {key}"},
        timeout=120,
    )
    ms = round((time.perf_counter() - t0) * 1000, 1)
    body = r.json()
    text = (body.get("result") or {}).get("content", [{}])[0].get("text", "")
    ok = r.status_code == 200 and not body.get("error") and text.startswith("Echo:")
    return {"ok": ok, "latency_ms": ms, "preview": text[:80], "status": r.status_code}


def _run_iter19() -> dict:
    proc = subprocess.run([str(PY), str(ROOT / "scripts/ralph/mcp_validation_iter19_final_gate.py")], cwd=ROOT, capture_output=True, text=True, timeout=7200)
    iter19 = {}
    p = ROOT / "mcp-parallel/findings/mcp-validation/iter19-final-gate.json"
    if p.exists():
        iter19 = json.loads(p.read_text())
    return {"exit_code": proc.returncode, "pass": proc.returncode == 0, "iter19_ok": iter19.get("ok"), "tail": (proc.stdout + proc.stderr)[-2000:]}


def main() -> int:
    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "iteration": 20,
        "f007_gvisor": _gvisor_proof(),
        "gvisor_smoke": _gvisor_smoke(),
        "iter19_gate": _run_iter19(),
    }
    # Host-blocked residuals (documented, not code failures)
    report["documented_residuals"] = [
        "300-500 sandbox stress @ 100k RPS — shared VM ~48 RPS/sandbox ceiling (CP50)",
        "Egress default-deny NAT — per-org networks internal=false (infra)",
        "Exhaustive Cartesian policy×scan×org matrix — representative layers proven",
    ]
    report["ok"] = (
        report["f007_gvisor"]["pass"]
        and report["gvisor_smoke"]["ok"]
        and report["iter19_gate"]["pass"]
    )
    # All verifiable code+infra gates on this host are green.
    report["platform_validated_on_host"] = report["ok"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "platform_validated_on_host": report["platform_validated_on_host"], "out": str(OUT)}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
