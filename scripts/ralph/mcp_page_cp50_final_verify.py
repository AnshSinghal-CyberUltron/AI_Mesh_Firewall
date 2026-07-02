#!/usr/bin/env python3
"""MCP-page Ralph — CP50: FINAL stress+verify, GREEN 3× consecutive.

The completion gate. Runs the CP47 load driver 3 times back-to-back against the
FULL 15-MCP fleet (3 orgs × 5 everything-servers) at a representative sustained
concurrency, and requires EVERY run to be green on ALL of:

  * drove the load     — calls_made >= 95% of target
  * ~0 failures        — drops == 0 AND no 5xx in the status histogram
  * concurrency-correct — id_mismatch == 0, content_mismatch == 0, arith_mismatch == 0
  * cross-tenant clean  — cross_tenant == 0 AND canary_leak == 0 (each org plants a
                          unique secret in its echoes; no reply carries another org's)

A single non-green run fails CP50 (never fakes green). Concurrency is kept in the
sandbox's proven-safe zone (CP48: 32-in-flight fleet + 3-org xtenant were clean) so
the gate reflects SUSTAINED load, not a pathological back-to-back overload burst.

Env: RUNS (default 3), TARGET_CALLS (default 3000), WORKERS (default 6), CONN
     (default 8) → 48 in-flight across 15 MCPs ≈ 3/server.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "..", "..", "gateway", ".venv", "bin", "python")
HARNESS = os.path.join(HERE, "mcp_page_cp47_stress.py")
OUTDIR = os.path.join(HERE, "..", "..", "mcp-parallel", "findings", "mcp-page", "cp50")

RUNS = int(os.environ.get("RUNS", "3"))
TARGET_CALLS = int(os.environ.get("TARGET_CALLS", "3000"))
WORKERS = int(os.environ.get("WORKERS", "6"))
CONN = int(os.environ.get("CONN", "8"))


def _green(r: dict) -> tuple[bool, list[str]]:
    fails = []
    iso = r.get("isolation", {})
    if r.get("calls_made", 0) < int(TARGET_CALLS * 0.95):
        fails.append(f"under-drove {r.get('calls_made')}/{TARGET_CALLS}")
    if r.get("drops", 1) != 0:
        fails.append(f"drops={r.get('drops')}")
    hist = r.get("status_hist", {}) or {}
    server_errs = sum(v for k, v in hist.items() if k in ("500", "502", "503"))
    if server_errs:
        fails.append(f"5xx={server_errs}")
    for k in ("id_mismatch", "content_mismatch", "arith_mismatch", "cross_tenant"):
        if iso.get(k, 1) != 0:
            fails.append(f"{k}={iso.get(k)}")
    if r.get("canary_leak", 1) != 0:
        fails.append(f"canary_leak={r.get('canary_leak')}")
    return (not fails), fails


def run_once(idx: int) -> dict:
    out = os.path.join(OUTDIR, f"run{idx}.json")
    env = {**os.environ, "WORKERS": str(WORKERS), "CONN": str(CONN),
           "TARGET_CALLS": str(TARGET_CALLS), "ORG_FILTER": "", "SERVER_FILTER": "",
           "DURATION_S": "0", "MAX_FAIL_RATE": "0.01", "RETRIES": "2", "OUT": out}
    subprocess.run([PY, HARNESS], env=env, capture_output=True, text=True, timeout=400)
    return json.load(open(out, encoding="utf-8"))


def main() -> int:
    os.makedirs(OUTDIR, exist_ok=True)
    results = []
    green_streak = 0
    for i in range(1, RUNS + 1):
        r = run_once(i)
        ok, fails = _green(r)
        results.append({"run": i, "green": ok, "fails": fails,
                        "rps": r.get("achieved_rps"), "made": r.get("calls_made"),
                        "drops": r.get("drops"), "transient_recovered": r.get("transient_recovered"),
                        "p50": r.get("latency_ms", {}).get("p50"),
                        "p99": r.get("latency_ms", {}).get("p99"),
                        "canary_leak": r.get("canary_leak"), "status_hist": r.get("status_hist"),
                        "isolation": r.get("isolation")})
        green_streak = green_streak + 1 if ok else 0
        print(f"run {i}/{RUNS}: {'GREEN' if ok else 'RED ' + str(fails)} "
              f"rps={r.get('achieved_rps')} made={r.get('calls_made')} drops={r.get('drops')} "
              f"recovered_by_retry={r.get('transient_recovered')} "
              f"p99={r.get('latency_ms', {}).get('p99')}ms canary_leak={r.get('canary_leak')} "
              f"iso={r.get('isolation')}")

    all_green = all(x["green"] for x in results) and len(results) == RUNS
    report = {"checkpoint": "50", "runs": RUNS, "target_calls": TARGET_CALLS,
              "inflight": WORKERS * CONN, "green_streak": green_streak,
              "all_green_3x": all_green, "results": results}
    json.dump(report, open(os.path.join(OUTDIR, "final_verify.json"), "w", encoding="utf-8"), indent=2)
    print(f"\nCP50: {'GREEN 3x/3x — FINAL STRESS+VERIFY PASS' if all_green else 'FAIL (not green 3x consecutive)'} "
          f"(green_streak={green_streak}/{RUNS})")
    return 0 if all_green else 1


if __name__ == "__main__":
    sys.exit(main())
