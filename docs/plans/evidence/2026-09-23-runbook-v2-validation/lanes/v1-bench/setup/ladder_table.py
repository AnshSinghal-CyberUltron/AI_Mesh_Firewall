#!/usr/bin/env python3
"""Build the per-step table from each run's recomputed outputs (analysis/summary.json from harness
analyze.py, sut_resources.json, lg-v1aug/v1_adapter_report.json). usage: ladder_table.py RUN [RUN...]"""
import json, sys
from pathlib import Path
E = Path("/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs")


def g(d, *ks):
    for k in ks:
        if d is None:
            return None
        d = d.get(k) if isinstance(d, dict) else None
    return d


def f(x, nd=1):
    return "-" if x is None else (f"{x:.{nd}f}" if isinstance(x, (int, float)) else str(x))


rows = []
for run in sys.argv[1:]:
    R = E / run
    s = json.loads((R / "analysis" / "summary.json").read_text())
    res = json.loads((R / "sut_resources.json").read_text()) if (R / "sut_resources.json").exists() else {}
    ad = json.loads((R / "lg-v1aug" / "v1_adapter_report.json").read_text()) if (R / "lg-v1aug" / "v1_adapter_report.json").exists() else {}
    gw = g(res, "containers", "aimeshperf-gateway-1") or {}
    checks = s.get("checks") or s.get("pass_checks") or {}
    rows.append({
        "run": run, "offered_rps": s.get("achieved_offered_rps"), "qualified_rps": s.get("qualified_rps"),
        "offered": s.get("offered"), "qualified": s.get("qualified"), "errors": s.get("errors"),
        "error_rate": s.get("error_rate"), "error_reasons": s.get("error_reasons"),
        "drops": g(s, "schedule", "drops"), "late_p99_ms": g(s, "schedule", "lateness", "p99"),
        "fw_p50": g(s, "T_fw_addon", "p50"), "fw_p90": g(s, "T_fw_addon", "p90"), "fw_p99": g(s, "T_fw_addon", "p99"),
        "fw_p999": g(s, "T_fw_addon", "p999"),
        "sse_p50": g(s, "T_fw_addon_sse", "p50"), "sse_p99": g(s, "T_fw_addon_sse", "p99"),
        "json_p50": g(s, "T_fw_addon_json", "p50"), "json_p99": g(s, "T_fw_addon_json", "p99"),
        "json_p999": g(s, "T_fw_addon_json", "p999"),
        "total_p50": g(s, "T_addon_total", "p50"), "total_p99": g(s, "T_addon_total", "p99"),
        "first_p50": g(s, "T_addon_first", "p50"), "first_p99": g(s, "T_addon_first", "p99"),
        "lag_p50": g(s, "T_release_lag_max", "p50"), "lag_p99": g(s, "T_release_lag_max", "p99"),
        "gw_cpu_ms_req": gw.get("cpu_ms_per_request"), "stack_cpu_ms_req": res.get("stack_cpu_ms_per_request"),
        "gw_cores": gw.get("cores_mean"), "gw_cores_1s_max": gw.get("cores_1s_max"),
        "host_util_pct": res.get("host_cpu_util_pct"), "gw_anon_mib_max": gw.get("mem_anon_mib_max"),
        "worker_rss_mib_max": g(res, "gateway_worker_rss_mib", "max"),
        "worker_cores": res.get("gateway_worker_cores"), "probe_p99": g(res, "health_probe_ms", "p99"),
        "probe_max": g(res, "health_probe_ms", "max"), "probe_fail": res.get("health_probe_failures"),
        "audit_completeness": ad.get("audit_completeness"), "verdict": s.get("verdict") or s.get("result"),
        "valid": g(s, "valid"), "checks": checks,
    })
hdr = ("| run | offered rps | qualified rps | err (rate) | drops | T_fw p50 | p90 | p99 | p99.9 | SSE p99 | JSON p50 | JSON p99 | "
       "T_addon_total p99 | GW CPU ms/req | stack CPU ms/req | GW cores | host util % | GW anon MiB | /health p99 / max ms | audit |")
print(hdr)
print("|" + "---|" * (hdr.count("|") - 1))
for r in rows:
    print(f"| {r['run']} | {f(r['offered_rps'],2)} | {f(r['qualified_rps'],2)} | {r['errors']} ({f(r['error_rate'],4)}) | {r['drops']} | "
          f"{f(r['fw_p50'])} | {f(r['fw_p90'])} | {f(r['fw_p99'])} | {f(r['fw_p999'])} | {f(r['sse_p99'])} | {f(r['json_p50'])} | "
          f"{f(r['json_p99'])} | {f(r['total_p99'])} | {f(r['gw_cpu_ms_req'])} | {f(r['stack_cpu_ms_req'])} | {f(r['gw_cores'],2)} | "
          f"{f(r['host_util_pct'])} | {f(r['gw_anon_mib_max'],0)} | {f(r['probe_p99'])} / {f(r['probe_max'])} | {f(r['audit_completeness'],4)} |")
json.dump(rows, open(E / ("table-" + "_".join(sys.argv[1:])[:120] + ".json"), "w"), indent=1)
