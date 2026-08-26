#!/usr/bin/env python3
"""T-C1b: four heavy 30d endpoints at once; cgroup < 70% of memory.max."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
CONTAINER = os.environ.get("CONTROL_CONTAINER", "ai_mesh_firewall-control-1")
OUT = os.environ.get("TC1B_OUT", "mcp-parallel/findings/phase0a/tc1b.json")

ENDPOINTS = (
    "/api/security/soc-kpis/?period=30d",
    "/api/security/module-kpis/?period=30d",
    "/api/security/attack-vector-trends/?period=30d",
    "/api/security/module-trends/?period=30d",
)


def _read(path: str) -> str:
    import subprocess

    return subprocess.check_output(
        ["docker", "exec", CONTAINER, "cat", path],
        text=True,
    ).strip()


def login() -> str:
    payload = json.dumps({"email": EMAIL, "password": PASSWORD}).encode()
    req = urllib.request.Request(
        f"{CONTROL}/api/auth/token/",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access"]


def fetch(token: str, path: str) -> dict:
    t0 = time.perf_counter()
    req = urllib.request.Request(
        f"{CONTROL}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
            return {
                "path": path,
                "status": resp.status,
                "bytes": len(body),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        snippet = raw[:200].decode("utf-8", "replace")
        return {
            "path": path,
            "status": exc.code,
            "bytes": len(raw),
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "snippet": snippet,
        }


def main() -> int:
    mem_max = int(_read("/sys/fs/cgroup/memory.max"))
    cap = int(mem_max * 0.70)
    samples = []
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            samples.append(int(_read("/sys/fs/cgroup/memory.current")))
            time.sleep(0.2)

    token = login()
    baseline = int(_read("/sys/fs/cgroup/memory.current"))
    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(fetch, token, p) for p in ENDPOINTS]
        for fut in as_completed(futs):
            results.append(fut.result())
    stop.set()
    th.join(timeout=2)
    peak = max(samples) if samples else baseline
    oom = False
    try:
        import subprocess

        inspect = json.loads(
            subprocess.check_output(
                ["docker", "inspect", "-f", "{{json .State}}", CONTAINER],
                text=True,
            )
        )
        if isinstance(inspect, str):
            inspect = json.loads(inspect)
        oom = bool(inspect.get("OOMKilled"))
    except Exception as exc:  # noqa: BLE001
        inspect = {"error": str(exc)}

    returned = all(r.get("status") for r in results)
    under_cap = peak < cap
    ok = returned and under_cap and not oom and len(results) == 4
    report = {
        "ok": ok,
        "mem_max": mem_max,
        "cap_70pct": cap,
        "baseline": baseline,
        "peak": peak,
        "under_70pct": under_cap,
        "oom_killed": oom,
        "results": results,
        "state": inspect,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps({k: report[k] for k in ("ok", "peak", "cap_70pct", "under_70pct", "oom_killed")}))
    print(json.dumps(results))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
