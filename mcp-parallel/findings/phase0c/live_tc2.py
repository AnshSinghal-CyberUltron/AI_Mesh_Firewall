#!/usr/bin/env python3
"""Phase 0c T-C2 live HTTP: 20-sample matrix + 30d p99 after ANALYTICS_SERVE_ROLLUPS=1."""

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
OUT = os.environ.get("TC2_OUT", "mcp-parallel/findings/phase0c/tc2_http.json")
N = int(os.environ.get("TC2_N", "11"))
CADENCE_S = float(os.environ.get("TC2_CADENCE_S", "30"))
POLL_S = float(os.environ.get("TC2_POLL_S", "5"))

PERIODS = ("1h", "6h", "24h", "7d", "30d")
ENDPOINTS = (
    "/api/security/soc-kpis/?period={p}",
    "/api/security/module-kpis/?period={p}",
    "/api/security/attack-vector-trends/?period={p}",
    "/api/security/module-trends/?period={p}",
)
HEAVIES_30D = tuple(ep.format(p="30d") for ep in ENDPOINTS)


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
    idx = min(len(ordered) - 1, max(0, int(0.99 * len(ordered) + 0.999999) - 1))
    return ordered[idx]


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
    }


def flag_from_container() -> str:
    try:
        return subprocess.check_output(
            [
                "docker",
                "exec",
                "ai_mesh_firewall-control-1",
                "printenv",
                "ANALYTICS_SERVE_ROLLUPS",
            ],
            text=True,
            timeout=10,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return f"error:{exc}"


def main() -> int:
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    flag = flag_from_container()
    token = login()

    matrix = []
    for period in PERIODS:
        for ep in ENDPOINTS:
            matrix.append(fetch(token, ep.format(p=period)))

    sequential = {}
    for path in HEAVIES_30D:
        sequential[path] = summarize([fetch(token, path) for _ in range(N)])

    cadence_rows = []
    t_end = time.time() + CADENCE_S
    pair = (HEAVIES_30D[0], HEAVIES_30D[1])
    while time.time() < t_end:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(fetch, token, p) for p in pair]
            batch = [fut.result() for fut in as_completed(futs)]
        cadence_rows.append({"t": round(CADENCE_S - (t_end - time.time()), 1), "batch": batch})
        remaining = t_end - time.time()
        if remaining > 0:
            time.sleep(min(POLL_S, remaining))
        pair = (HEAVIES_30D[2], HEAVIES_30D[3]) if pair[0] == HEAVIES_30D[0] else (HEAVIES_30D[0], HEAVIES_30D[1])

    invalid = fetch(token, "/api/security/soc-kpis/?period=90d")
    health = fetch(token, "/api/health/")
    all_p99 = {path: body["p99_ms"] for path, body in sequential.items()}
    worst_p99 = max(all_p99.values()) if all_p99 else 0.0
    matrix_ok = all(200 <= int(r.get("status") or 0) < 300 for r in matrix)
    cadence_ok = all(
        200 <= int(item.get("status") or 0) < 300 for poll in cadence_rows for item in poll["batch"]
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate": "T-C2 live HTTP after ANALYTICS_SERVE_ROLLUPS=1",
        "analytics_serve_rollups": flag,
        "matrix_n": len(matrix),
        "matrix": matrix,
        "matrix_all_2xx": matrix_ok,
        "sequential_30d": sequential,
        "all_p99_ms": all_p99,
        "worst_p99_ms": worst_p99,
        "baseline_tch1_worst_p99_ms": 3717.4,
        "cadence": {"polls": len(cadence_rows), "all_2xx": cadence_ok, "rows": cadence_rows},
        "invalid_90d": invalid,
        "health": health,
        "ok": matrix_ok
        and cadence_ok
        and all(body["all_2xx"] for body in sequential.values())
        and int(invalid.get("status") or 0) == 400
        and 200 <= int(health.get("status") or 0) < 300
        and flag in {"1", "true", "yes"},
    }
    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=2)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "flag": flag,
                "matrix_n": len(matrix),
                "matrix_all_2xx": matrix_ok,
                "worst_p99_ms": worst_p99,
                "all_p99_ms": all_p99,
                "invalid_90d": invalid.get("status"),
                "health": health.get("status"),
            },
            indent=2,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
