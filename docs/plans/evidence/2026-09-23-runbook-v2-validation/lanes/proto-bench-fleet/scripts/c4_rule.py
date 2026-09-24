#!/usr/bin/env python3
"""Controller knee rule for the unbounded re-run: a step PASSES iff C4 p99 < 20 ms (reviewer-observability
definition, p99 over every non-policy-block request with infra/unqualified = +inf, 100% of streams) AND
infra <= 0.1% of offered AND 0 schedule drops AND 0 safety failures AND run valid.
The load rule (qualified-only T_addon_total p99) and the strict T_fw_addon (incl. SSE holdback) are
reported beside it. Also per-worker CPU skew per unit and established connections per worker (ss).
  c4_rule.py RUN_DIR   -> RUN_DIR/c4_rule.json + one summary line"""
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
s = json.loads((run / "pbf_summary.json").read_text())
c4 = json.loads((run / "c4.json").read_text()) if (run / "c4.json").exists() else {}
step = s.get("step") or {}
C = c4.get("C4_T_fw_addon_ABC") or {}
c4p99 = C.get("p99")
infra = s.get("infra_error_rate") or 0.0
checks = {"c4_p99_lt_20": c4p99 is not None and c4p99 != float("inf") and c4p99 < 20.0,
          "infra_le_0.1pct": infra <= 0.001, "zero_drops": s.get("drops") == 0,
          "zero_safety": s.get("safety_failures") == 0, "valid": bool(s.get("valid"))}
us = s.get("units_side") or {}
skew = {}
for u, row in (us.get("per_unit") or {}).items():
    w = sorted((row.get("cpu") or {}).get("per_proc_util", {}).get("worker", []))
    conns = None
    ssf = run / u / "sut" / "snap-meas_end" / "ss_conns.json"
    if ssf.exists():
        d = json.loads(ssf.read_text())
        v = sorted(d.get("per_pid", {}).values())
        conns = {"total": d.get("total"), "workers_with_conns": len(v), "min": v[0] if v else 0,
                 "median": v[len(v) // 2] if v else 0, "max": v[-1] if v else 0}
    skew[u] = {"worker_cpu_busiest": w[-1] if w else None, "worker_cpu_median": w[len(w) // 2] if w else None,
               "worker_cpu_min": w[0] if w else None, "conns_per_worker_meas_end": conns,
               "admitted_max_over_mean": (row.get("per_worker_admitted") or {}).get("max_over_mean"),
               "gpu_sm_mean": [g["sm_mean"] for g in (row.get("gpu") or {}).values()]}
wc = us.get("worker_counts") or {}
gpus = [g for v in skew.values() for g in v["gpu_sm_mean"]]
out = {
    "run": run.name, "units": len((step.get("units") or "").split()), "rate": step.get("rate"),
    "pass_c4_rule": all(checks.values()), "checks": checks, "pass_load_rule": s.get("load_knee_pass"),
    "qualified_rps": s.get("qualified_rps"), "infra_pct": round(100 * infra, 4), "infra_reasons": s.get("infra_reasons"),
    "sem_U_blocks": (s.get("infra_reasons") or {}).get("block_on_unavailable_sem", 0),
    "guard_deadline_expired": wc.get("guard_deadline_expired", 0), "guard_unavailable_findings": wc.get("guard_unavailable_findings", 0),
    "sheds": {k: v for k, v in wc.items() if k.startswith("shed{")}, "fp_block_pct": round(100 * (s.get("fp_rate") or 0), 3),
    "C4_p99_ms_offered_inf": c4p99, "C4_p50_ms": C.get("p50"), "C4_n": C.get("n"), "C4_n_inf": C.get("n_inf"),
    "C4_sse_p99": (c4.get("C4_sse") or {}).get("p99"), "C4_json_p99": (c4.get("C4_json") or {}).get("p99"),
    "c4_counts": c4.get("counts"),
    "json_p99_ms_qualified": s["T_addon_total_json"].get("p99"), "sse_total_p99_ms_qualified": s["T_addon_total_sse"].get("p99"),
    "load_rule_p99_T_addon_total": s["T_fw_addon_nohold"].get("p99"), "strict_T_fw_addon_p99_qualified": s["T_fw_addon"].get("p99"),
    "gpu_sm_mean_range": [min(gpus), max(gpus)] if gpus else None,
    "cpu_ms_per_req": (us.get("cpu_ms_per_req") or {}).get("gateway"), "gateway_cores": us.get("gateway_cores_total"),
    "per_unit_skew": skew, "drops": s.get("drops"), "lateness_max_ms": s["lateness"].get("max"),
    "loadgen_cpu_max": max((x.get("busy_max") or 0) for x in s.get("loadgen") or [{}]),
    "redis_ops_s": (s.get("redis_side") or {}).get("ops_per_s"),
    "redis_ping_p99_us": ((s.get("redis_side") or {}).get("ping_rtt_us") or {}).get("p99_us"),
    "edge_nginx_cores": (((s.get("edge_side") or {}).get("cpu") or {}).get("cores_by_role") or {}).get("nginx"),
}
(run / "c4_rule.json").write_text(json.dumps(out, indent=1) + "\n")
sk = "; ".join(f"{u.split('-')[-1]}: cpu {v['worker_cpu_busiest']}/{v['worker_cpu_median']}/{v['worker_cpu_min']} "
               f"conns {(v['conns_per_worker_meas_end'] or {}).get('max')}/{(v['conns_per_worker_meas_end'] or {}).get('median')}/{(v['conns_per_worker_meas_end'] or {}).get('min')}"
               for u, v in skew.items())
print(f"C4RULE {run.name}: {'PASS' if out['pass_c4_rule'] else 'FAIL'} (load-rule {'PASS' if out['pass_load_rule'] else 'FAIL'}) "
      f"units={out['units']} rate={out['rate']} qRPS={out['qualified_rps']} C4p99={c4p99} (n={C.get('n')} inf={C.get('n_inf')}) "
      f"JSONp99={out['json_p99_ms_qualified']} SSEtot p99={out['sse_total_p99_ms_qualified']} strict p99={out['strict_T_fw_addon_p99_qualified']} "
      f"infra={out['infra_pct']}% semU={out['sem_U_blocks']} sheds={out['sheds']} GPU={out['gpu_sm_mean_range']} cpu-ms={out['cpu_ms_per_req']} | skew {sk}")
