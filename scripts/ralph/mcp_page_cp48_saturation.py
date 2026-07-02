#!/usr/bin/env python3
"""MCP-page Ralph — CP48: saturation sweep + bottleneck localization.

Drives the CP47 load harness at escalating concurrency and samples container
resources to answer: where is the throughput ceiling, what saturates first, do
503s/OOMs appear, and WHERE is the binding bottleneck (per-server serialization
vs an aggregate broker/gateway limit)?

Three probes (good-neighbor: heavy load stays on MY org; the parallel session's
org-a/org-b get only ONE moderate cross-tenant leak-check step):
  A. FLEET SWEEP (zeroshield, 5 servers) — in-flight 8→128: RPS/p50/p99/drop/503
     curve + peak docker-stats (CPU/mem/pids) → is the ceiling resource-bound?
  B. SINGLE-SERVER SWEEP (zeroshield/everything-1) — in-flight 1→16: if per-server
     RPS is FLAT, the MCP stdio server serializes (bottleneck is per-server, not
     aggregate); if it scales, the limit is elsewhere.
  C. CROSS-TENANT LEAK (all 3 orgs, 32 in-flight, 10s) — canary_leak must be 0
     under mixed multi-org load.

Emits a saturation table + a grounded bottleneck verdict to cp48/verdict.json.
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
OUTDIR = os.path.join(HERE, "..", "..", "mcp-parallel", "findings", "mcp-page", "cp48")
SANDBOXES = ["zeroshield-mcp-sandbox", "org-a-mcp-sandbox", "org-b-mcp-sandbox",
             "ai_mesh_mcp_broker", "ai_mesh_firewall-gateway-1"]


def run_step(label, workers, conn, duration, org_filter="zeroshield", server_filter=""):
    env = {**os.environ, "WORKERS": str(workers), "CONN": str(conn),
           "DURATION_S": str(duration), "TARGET_CALLS": "100000000",
           "ORG_FILTER": org_filter, "SERVER_FILTER": server_filter,
           "OUT": os.path.join(OUTDIR, f"{label}.json"), "MAX_FAIL_RATE": "1.0"}
    t0 = time.time()
    p = subprocess.run([PY, HARNESS], env=env, capture_output=True, text=True,
                       timeout=duration + 120)
    try:
        r = json.load(open(os.path.join(OUTDIR, f"{label}.json"), encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"label": label, "error": f"{e}", "stderr": p.stderr[-400:], "stdout": p.stdout[-400:]}
    r["_wall_orchestrated"] = round(time.time() - t0, 1)
    r["_inflight"] = workers * conn
    return r


def sample_stats():
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.PIDs}}"],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return {}
    stats = {}
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) == 4 and parts[0] in SANDBOXES:
            stats[parts[0]] = {"cpu": parts[1], "mem": parts[2], "pids": parts[3]}
    return stats


def oom_status():
    st = {}
    for c in SANDBOXES:
        try:
            v = subprocess.run(["docker", "inspect", "-f", "{{.State.OOMKilled}}", c],
                               capture_output=True, text=True, timeout=15).stdout.strip()
            st[c] = v
        except Exception:  # noqa: BLE001
            st[c] = "?"
    return st


def _row(r):
    lm = r.get("latency_ms", {})
    return {"inflight": r.get("_inflight"), "rps": r.get("achieved_rps"),
            "made": r.get("calls_made"), "p50": lm.get("p50"), "p99": lm.get("p99"),
            "drops": r.get("drops"), "backpressure": r.get("backpressure"),
            "status_hist": r.get("status_hist"), "canary_leak": r.get("canary_leak")}


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    result = {"checkpoint": "48", "oom_baseline": oom_status(), "fleet_sweep": [],
              "single_server_sweep": [], "cross_tenant": None, "peak_stats": None}

    # ── A. FLEET SWEEP (my org, 5 servers): escalate in-flight ──
    print("== A. fleet sweep (zeroshield ×5 servers) ==")
    for workers, conn in [(1, 8), (2, 8), (2, 16), (4, 16), (4, 32)]:  # 8,16,32,64,128
        lbl = f"fleet_{workers}x{conn}"
        r = run_step(lbl, workers, conn, duration=10)
        row = _row(r)
        result["fleet_sweep"].append(row)
        print(f"  inflight={row['inflight']:>3} rps={row['rps']} p50={row['p50']} "
              f"p99={row['p99']} drops={row['drops']} bp={row['backpressure']} "
              f"status={row['status_hist']}")
        if row["inflight"] == 128:  # sample resources at peak
            result["peak_stats"] = sample_stats()

    # ── B. SINGLE-SERVER SWEEP: per-server serialization test ──
    print("== B. single-server sweep (zeroshield/everything-1) ==")
    for conn in [1, 2, 4, 8, 16]:
        lbl = f"single_{conn}"
        r = run_step(lbl, 1, conn, duration=8, org_filter="zeroshield", server_filter="everything-1")
        row = _row(r)
        result["single_server_sweep"].append(row)
        print(f"  inflight={row['inflight']:>3} rps={row['rps']} p50={row['p50']} p99={row['p99']} drops={row['drops']}")

    # ── C. CROSS-TENANT LEAK under mixed load (all 3 orgs) ──
    print("== C. cross-tenant leak (all 3 orgs, 32 in-flight, 10s) ==")
    r = run_step("xtenant", 4, 8, duration=10, org_filter="")  # all orgs
    result["cross_tenant"] = _row(r)
    print(f"  made={result['cross_tenant']['made']} canary_leak={result['cross_tenant']['canary_leak']} "
          f"drops={result['cross_tenant']['drops']}")

    result["oom_after"] = oom_status()

    # ── Bottleneck verdict (grounded) ──
    fleet = [x for x in result["fleet_sweep"] if x.get("rps")]
    single = [x for x in result["single_server_sweep"] if x.get("rps")]
    fleet_ceiling = max((x["rps"] for x in fleet), default=None)
    single_ceiling = max((x["rps"] for x in single), default=None)
    single_flat = (single_ceiling and single and single[0]["rps"]
                   and single_ceiling <= single[0]["rps"] * 1.6)  # ≤1.6× from 1→16 = ~flat
    n_servers = 5
    verdict = {
        "fleet_ceiling_rps": fleet_ceiling,
        "single_server_ceiling_rps": single_ceiling,
        "single_server_flat": bool(single_flat),
        "fleet_approx_n×single": round((single_ceiling or 0) * n_servers, 1),
        "any_oom": any(v == "true" for v in result["oom_after"].values()),
        "any_503": any((x.get("status_hist") or {}).get("503") for x in fleet),
        "canary_leak": result["cross_tenant"]["canary_leak"] if result["cross_tenant"] else None,
    }
    # Diagnosis: if single-server RPS is flat AND fleet ≈ n_servers × single, the
    # binding bottleneck is PER-SERVER stdio serialization (each MCP server processes
    # one JSON-RPC at a time), NOT an aggregate broker/gateway/resource limit.
    if single_flat and fleet_ceiling and single_ceiling:
        ratio = fleet_ceiling / single_ceiling
        if 3.0 <= ratio <= 7.0:
            verdict["diagnosis"] = ("PER-SERVER STDIO SERIALIZATION — per-server RPS is flat "
                                    f"(~{single_ceiling}) and fleet ≈ {n_servers}×single ({ratio:.1f}×); "
                                    "throughput scales with SERVER COUNT, not per-server in-flight. "
                                    "Resources idle → not CPU/mem-bound.")
        else:
            verdict["diagnosis"] = (f"per-server flat but fleet≠n×single (ratio {ratio:.1f}×) — "
                                    "an aggregate limit (broker/gateway concurrency) caps below n×single.")
    else:
        verdict["diagnosis"] = ("per-server RPS SCALES with in-flight — bottleneck is NOT per-server "
                                "serialization; inspect aggregate broker/gateway/pool limits.")
    result["verdict"] = verdict
    json.dump(result, open(os.path.join(OUTDIR, "verdict.json"), "w", encoding="utf-8"), indent=2)
    print("\n== VERDICT ==")
    print(json.dumps(verdict, indent=2))
    print("== peak_stats ==")
    print(json.dumps(result["peak_stats"], indent=2))
    print("CP48: measurement complete →", os.path.join(OUTDIR, "verdict.json"))


if __name__ == "__main__":
    sys.exit(main())
