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
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        return {
            "path": path,
            "status": 0,
            "bytes": 0,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "error": type(exc).__name__,
            "snippet": str(exc)[:200],
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

    def _oom_kill_count() -> int:
        events = _read("/sys/fs/cgroup/memory.events")
        for line in events.splitlines():
            if line.startswith("oom_kill "):
                return int(line.split()[1])
        return 0

    oom_kill_before = _oom_kill_count()
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

    oom_kill_after = _oom_kill_count()
    oom_delta = oom_kill_after - oom_kill_before
    # docker inspect OOMKilled stays true after a prior restart of this
    # container; T-C1b must use the cgroup counter delta instead.
    returned = all(200 <= int(r.get("status") or 0) < 300 for r in results)
    under_cap = peak < cap
    ok = returned and under_cap and oom_delta == 0 and len(results) == 4
    report = {
        "ok": ok,
        "mem_max": mem_max,
        "cap_70pct": cap,
        "baseline": baseline,
        "peak": peak,
        "under_70pct": under_cap,
        "oom_killed_inspect": oom,
        "oom_kill_before": oom_kill_before,
        "oom_kill_after": oom_kill_after,
        "oom_kill_delta": oom_delta,
        "results": results,
        "state": inspect,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps({k: report[k] for k in ("ok", "peak", "cap_70pct", "under_70pct", "oom_kill_delta")}))
    print(json.dumps(results))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
