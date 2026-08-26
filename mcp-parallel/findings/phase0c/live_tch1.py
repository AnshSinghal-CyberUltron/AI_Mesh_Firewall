#!/usr/bin/env python3
"""Phase 0c.1 / T-CH1: 30-day GROUP BY p99 and DB CPU under Overview cadence.

C-2 rollups are funded only if p99 > 5000 ms or postgres CPU > 70%.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
PG_CONTAINER = os.environ.get("POSTGRES_CONTAINER", "ai_mesh_firewall-postgres-1")
OUT = os.environ.get("TCH1_OUT", "mcp-parallel/findings/phase0c/tch1.json")
N = int(os.environ.get("TCH1_N", "11"))
CADENCE_S = float(os.environ.get("TCH1_CADENCE_S", "120"))
POLL_S = float(os.environ.get("TCH1_POLL_S", "10"))
P99_LIMIT_MS = 5000.0
CPU_LIMIT_PCT = 70.0

HEAVIES = (
    "/api/security/soc-kpis/?period=30d",
    "/api/security/module-kpis/?period=30d",
    "/api/security/attack-vector-trends/?period=30d",
    "/api/security/module-trends/?period=30d",
)


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


def fetch(token: str, path: str, timeout: int = 30) -> dict:
    t0 = time.perf_counter()
    req = urllib.request.Request(
        f"{CONTROL}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return {
                "path": path,
                "status": resp.status,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "bytes": len(body),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return {
            "path": path,
            "status": exc.code,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "bytes": len(raw),
            "snippet": raw[:200].decode("utf-8", "replace"),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "path": path,
            "status": 0,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "bytes": 0,
            "error": type(exc).__name__,
            "snippet": str(exc)[:200],
        }


def p99_ms(samples: list[float]) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    # Honest small-n p99: nearest-rank, which is max for n=11.
    idx = min(len(ordered) - 1, max(0, int(0.99 * len(ordered) + 0.999999) - 1))
    return ordered[idx]


def docker_stats_cpu(container: str) -> dict:
    try:
        raw = subprocess.check_output(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}\t{{.MemUsage}}",
                container,
            ],
            text=True,
            timeout=15,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"error": str(exc)}
    parts = raw.split("\t")
    cpu = None
    mem = None
    if len(parts) >= 3:
        try:
            cpu = float(parts[1].replace("%", "").strip())
        except ValueError:
            cpu = None
        try:
            mem = float(parts[2].replace("%", "").strip())
        except ValueError:
            mem = None
    return {"raw": raw, "cpu_pct": cpu, "mem_pct": mem}


def summarize(rows: list[dict]) -> dict:
    times = [r["ms"] for r in rows]
    statuses = {}
    for r in rows:
        key = str(r["status"])
        statuses[key] = statuses.get(key, 0) + 1
    return {
        "n": len(times),
        "min_ms": min(times) if times else None,
        "median_ms": round(statistics.median(times), 1) if times else None,
        "p99_ms": round(p99_ms(times), 1) if times else None,
        "max_ms": max(times) if times else None,
        "all_2xx": all(200 <= int(r.get("status") or 0) < 300 for r in rows),
        "status_hist": statuses,
        "ms": times,
        "rows": rows,
    }


def main() -> int:
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    token = login()
    cpu_before = docker_stats_cpu(PG_CONTAINER)

    sequential = {}
    for path in HEAVIES:
        rows = [fetch(token, path) for _ in range(N)]
        sequential[path] = summarize(rows)

    cadence_rows = []
    cpu_samples = [cpu_before]
    t_end = time.time() + CADENCE_S
    pair = (HEAVIES[0], HEAVIES[1])
    while time.time() < t_end:
        cpu_samples.append(docker_stats_cpu(PG_CONTAINER))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(fetch, token, p) for p in pair]
            batch = [fut.result() for fut in as_completed(futs)]
        cadence_rows.append(
            {
                "t": round(CADENCE_S - (t_end - time.time()), 1),
                "batch": batch,
            }
        )
        remaining = t_end - time.time()
        if remaining > 0:
            time.sleep(min(POLL_S, remaining))
        pair = (HEAVIES[2], HEAVIES[3]) if pair[0] == HEAVIES[0] else (HEAVIES[0], HEAVIES[1])

    cpu_after = docker_stats_cpu(PG_CONTAINER)
    cpu_samples.append(cpu_after)
    cpu_vals = [s["cpu_pct"] for s in cpu_samples if isinstance(s.get("cpu_pct"), (int, float))]

    soc = sequential[HEAVIES[0]]
    all_p99 = {path: body["p99_ms"] for path, body in sequential.items()}
    worst_p99 = max(all_p99.values()) if all_p99 else 0.0
    peak_cpu = max(cpu_vals) if cpu_vals else None
    trigger_p99 = worst_p99 > P99_LIMIT_MS
    trigger_cpu = peak_cpu is not None and peak_cpu > CPU_LIMIT_PCT
    open_c2 = trigger_p99 or trigger_cpu

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate": "T-CH1 / 0c.1",
        "note": "C-2 rollups only if 30d GROUP BY p99 > 5s or postgres CPU > 70% under Overview cadence. ClickHouse is a later T-CH1 store, not this phase.",
        "p99_limit_ms": P99_LIMIT_MS,
        "cpu_limit_pct": CPU_LIMIT_PCT,
        "n_per_endpoint": N,
        "cadence_s": CADENCE_S,
        "poll_s": POLL_S,
        "postgres_container": PG_CONTAINER,
        "sequential_30d": sequential,
        "worst_p99_ms": worst_p99,
        "all_p99_ms": all_p99,
        "cadence": {
            "polls": len(cadence_rows),
            "rows": cadence_rows,
            "all_2xx": all(
                200 <= int(item.get("status") or 0) < 300
                for poll in cadence_rows
                for item in poll["batch"]
            ),
        },
        "postgres_cpu": {
            "before": cpu_before,
            "after": cpu_after,
            "samples": cpu_samples,
            "peak_pct": peak_cpu,
        },
        "trigger_p99": trigger_p99,
        "trigger_cpu": trigger_cpu,
        "open_c2_rollups": open_c2,
        "open_clickhouse": open_c2,
        "ok": (not open_c2)
        and soc["all_2xx"]
        and all(body["all_2xx"] for body in sequential.values()),
    }
    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=2)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "open_c2_rollups": open_c2,
                "worst_p99_ms": worst_p99,
                "peak_cpu_pct": peak_cpu,
                "all_p99_ms": all_p99,
                "soc_all_2xx": soc["all_2xx"],
            },
            indent=2,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
