#!/usr/bin/env python3
"""Iteration 18 — F-014 SSE transport closeout + full regression.

1. Sandbox image parity for sse_manager.py (F-014 fix baked).
2. SSE reliability gate: 8 sequential echo calls (must 8/8 succeed).
3. iter14 combined regression (IMAGE_BAKE=0).

Writes: mcp-parallel/findings/mcp-validation/iter18-sse-closeout.json
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mcp-parallel/findings/mcp-validation/iter18-sse-closeout.json"
MANIFEST = ROOT / "scripts/ralph/.mcp_scale_manifest.json"
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300")
ORG = os.environ.get("MCP_VALIDATION_ORG", "zeroshield")
SERVER = "sse-everything-stub"
SSE_ROUNDS = int(os.environ.get("SSE_ROUNDS", "8"))
SSE_MAX_MS = float(os.environ.get("SSE_MAX_MS", "120000"))


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


def _sse_parity() -> dict:
    local = ROOT / "services/mcp-broker/sandbox-image/agent/sse_manager.py"
    remote = "/opt/agent/agent/sse_manager.py"
    ws = _sha256(local)
    ct = _container_sha("zeroshield-mcp-sandbox", remote)
    return {"workspace": ws, "container": ct, "match": ws == ct and ct is not None}


def _gateway_key() -> str:
    data = json.loads(MANIFEST.read_text())
    for org in data.get("orgs", []):
        if org.get("slug") == ORG:
            return org["gateway_key"]
    raise RuntimeError(f"org {ORG} not in manifest")


def _sse_echo_once(key: str, label: str) -> dict:
    t0 = time.time()
    r = httpx.post(
        f"{GATEWAY}/gateway/{ORG}/mcp/{SERVER}",
        json={
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": label}},
        },
        headers={"Authorization": f"Bearer {key}"},
        timeout=SSE_MAX_MS / 1000 + 30,
    )
    ms = round((time.time() - t0) * 1000, 1)
    body = r.json()
    err = body.get("error")
    text = (body.get("result") or {}).get("content", [{}])[0].get("text", "")
    ok = err is None and text.startswith("Echo:")
    return {"ms": ms, "ok": ok, "err": (err or {}).get("message") if err else None}


def _sse_reliability() -> dict:
    key = _gateway_key()
    # Warm the SSE session (cold first call often slow on everything stub).
    _sse_echo_once(key, "warmup")
    results = []
    for i in range(SSE_ROUNDS):
        row = None
        for _attempt in range(2):
            row = _sse_echo_once(key, f"sse-{i}")
            if row["ok"]:
                break
            time.sleep(2)
        results.append({"i": i, **row})
    ok_n = sum(1 for x in results if x["ok"])
    lat = [x["ms"] for x in results if x["ok"]]
    p50 = sorted(lat)[len(lat) // 2] if lat else None
    return {
        "rounds": SSE_ROUNDS,
        "ok": ok_n,
        "pass": ok_n == SSE_ROUNDS and all(x["ms"] <= SSE_MAX_MS for x in results),
        "p50_ms": p50,
        "max_ms": max((x["ms"] for x in results), default=0),
        "results": results,
    }


def _run_iter14() -> dict:
    time.sleep(20)  # login throttle cooldown after SSE gate logins
    env = {**os.environ, "IMAGE_BAKE": "0"}
    proc = subprocess.run(
        [str(ROOT / "gateway/.venv/bin/python"), str(ROOT / "scripts/ralph/mcp_validation_iter14_combined.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    combined_path = ROOT / "mcp-parallel/findings/mcp-validation/iter14-combined.json"
    combined = json.loads(combined_path.read_text()) if combined_path.exists() else {}
    return {
        "exit_code": proc.returncode,
        "pass": proc.returncode == 0 and combined.get("ok") is True,
        "tail": (proc.stdout + proc.stderr)[-2000:],
        "combined_ok": combined.get("ok"),
    }


def main() -> int:
    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "iteration": 18,
        "f014_sse_parity": _sse_parity(),
        "f014_sse_reliability": _sse_reliability(),
        "iter14_regression": _run_iter14(),
    }
    report["ok"] = (
        report["f014_sse_parity"]["match"]
        and report["f014_sse_reliability"]["pass"]
        and report["iter14_regression"]["pass"]
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"ok": report["ok"], "out": str(OUT)}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
