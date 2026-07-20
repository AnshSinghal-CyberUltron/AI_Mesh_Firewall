#!/usr/bin/env python3
"""Sandbox kill/recreate chaos drill + live recovery for zeroshield.

1. Call tools/call on everything-1 (prove healthy).
2. docker stop+rm zeroshield-mcp-sandbox (chaos).
3. Call tools/call again — broker must re-ensure sandbox and succeed.
4. Inspect recovered container posture.

Writes mcp-parallel/findings/mcp-arch-validation-2026-07-08/sandbox-chaos-live.json
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-arch-validation-2026-07-08/sandbox-chaos-live.json"
MANIFEST = ROOT / "scripts/ralph/.mcp_scale_manifest.json"
GATEWAY = "http://127.0.0.1:8300"
ORG = "zeroshield"
SERVER = "everything-1"
CONTAINER = f"{ORG}-mcp-sandbox"


def sh(cmd: list[str], check: bool = True) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT) if check else (
        subprocess.run(cmd, text=True, capture_output=True).stdout
        + subprocess.run(cmd, text=True, capture_output=True).stderr
    )


def gw_echo(c: httpx.Client, key: str) -> dict:
    rid = f"chaos-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    r = c.post(
        f"{GATEWAY}/gateway/{ORG}/mcp/{SERVER}",
        json={
            "jsonrpc": "2.0",
            "id": rid,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": f"chaos-{rid}"}},
        },
        headers={"Authorization": f"Bearer {key}"},
        timeout=180,
    )
    ms = round((time.perf_counter() - t0) * 1000, 1)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    err = body.get("error")
    text = ((body.get("result") or {}).get("content") or [{}])[0].get("text", "")
    return {
        "status": r.status_code,
        "latency_ms": ms,
        "ok": r.status_code == 200 and not err,
        "error": err,
        "egress_preview": (text or str(err))[:160],
        "request_id": rid,
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    mf = json.loads(MANIFEST.read_text(encoding="utf-8"))
    key = next(o["gateway_key"] for o in mf["orgs"] if o["slug"] == ORG)
    report: dict = {"org": ORG, "server": SERVER, "phases": {}}

    with httpx.Client() as c:
        pre = gw_echo(c, key)
        report["phases"]["pre_call"] = pre
        print(f"pre_call ok={pre['ok']} {pre['latency_ms']}ms")

        # Chaos: stop + remove the org sandbox (volume labels preserved by broker).
        try:
            sh(["docker", "stop", CONTAINER])
            sh(["docker", "rm", "-f", CONTAINER])
            report["phases"]["chaos"] = {"stopped_and_removed": True}
        except subprocess.CalledProcessError as exc:
            report["phases"]["chaos"] = {"stopped_and_removed": False, "error": str(exc)}
            print("chaos failed:", exc)
            OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return 1

        # Confirm gone
        gone = subprocess.run(
            ["docker", "inspect", CONTAINER], capture_output=True, text=True
        )
        report["phases"]["inspect_after_rm"] = {"exit_code": gone.returncode}
        print(f"inspect_after_rm exit={gone.returncode}")

        # Recovery call — broker ensure + re-provision
        post = gw_echo(c, key)
        report["phases"]["post_call"] = post
        print(f"post_call ok={post['ok']} {post['latency_ms']}ms")

        # Posture of recovered container
        try:
            insp = json.loads(sh(["docker", "inspect", CONTAINER]))
            cfg = insp[0]
            labels = (cfg.get("Config") or {}).get("Labels") or {}
            host = cfg.get("HostConfig") or {}
            report["phases"]["recovered"] = {
                "running": (cfg.get("State") or {}).get("Running"),
                "label_org": labels.get("ai_mesh.org_slug") or labels.get("org_slug"),
                "label_role": labels.get("ai_mesh.role") or labels.get("role"),
                "cap_drop_all": "ALL" in (host.get("CapDrop") or []),
                "readonly_rootfs": bool(host.get("ReadonlyRootfs")),
            }
        except Exception as exc:
            report["phases"]["recovered"] = {"error": str(exc)}

    report["chaosPass"] = bool(
        report["phases"].get("pre_call", {}).get("ok")
        and report["phases"].get("post_call", {}).get("ok")
        and report["phases"].get("recovered", {}).get("running")
        and report["phases"].get("recovered", {}).get("label_org") == ORG
    )
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"chaosPass": report["chaosPass"]}, indent=2))
    return 0 if report["chaosPass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
