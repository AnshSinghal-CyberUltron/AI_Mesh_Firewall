#!/usr/bin/env python3
"""Per-run table for the unbounded (AMF_TARGET_P99_MS=1000, RV_GUARD_DEADLINE_MS=20) C4-rule re-run.
  ub_table.py [PREFIX ...]  (default ub)  -> markdown table from every RUN/c4_rule.json"""
import json
import sys
from pathlib import Path

RAW = Path("/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-fleet/runs")
pref = sys.argv[1:] or ["ub"]
cols = ["run", "units", "rate", "C4 rule", "load rule", "qualified RPS", "JSON p99", "SSE-total p99", "C4 p99 (offered, inf)",
        "strict T_fw p99", "infra %", "sem:U", "sheds", "FP-block %", "GPU SM %", "CPU-ms/req", "worker CPU busiest/median/min per unit",
        "conns/worker max/median/min per unit", "redis ops/s", "redis ping p99 us", "edge cores"]
print("| " + " | ".join(cols) + " |")
print("|" + "---|" * len(cols))
for d in sorted(RAW.iterdir()):
    f = d / "c4_rule.json"
    if not f.exists() or not any(d.name.startswith(p) for p in pref):
        continue
    r = json.loads(f.read_text())
    sk = r.get("per_unit_skew") or {}
    cpu = "; ".join(f"{v['worker_cpu_busiest']}/{v['worker_cpu_median']}/{v['worker_cpu_min']}" for v in sk.values())
    con = "; ".join(f"{(v['conns_per_worker_meas_end'] or {}).get('max')}/{(v['conns_per_worker_meas_end'] or {}).get('median')}/"
                    f"{(v['conns_per_worker_meas_end'] or {}).get('min')}" for v in sk.values())
    g = r.get("gpu_sm_mean_range")
    row = [r["run"], r["units"], r["rate"], "PASS" if r["pass_c4_rule"] else "FAIL", "PASS" if r["pass_load_rule"] else "FAIL",
           r["qualified_rps"], r["json_p99_ms_qualified"], r["sse_total_p99_ms_qualified"], r["C4_p99_ms_offered_inf"],
           r["strict_T_fw_addon_p99_qualified"], r["infra_pct"], r["sem_U_blocks"], sum((r.get("sheds") or {}).values()),
           r["fp_block_pct"], f"{g[0]}-{g[1]}" if g else "", r["cpu_ms_per_req"], cpu, con, r.get("redis_ops_s"),
           r.get("redis_ping_p99_us"), r.get("edge_nginx_cores")]
    print("| " + " | ".join("" if x is None else str(x) for x in row) + " |")
