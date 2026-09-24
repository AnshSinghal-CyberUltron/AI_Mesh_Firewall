#!/usr/bin/env python3
"""Second per-step table: SSE components, JSON, instrument health, v1 internal decomposition, worker skew."""
import json, sys
from pathlib import Path
E = Path("/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs")
def g(d, *ks):
    for k in ks:
        d = d.get(k) if isinstance(d, dict) else None
    return d
def f(x, nd=1):
    return "-" if x is None else f"{x:.{nd}f}"
print("| run | T_addon_first p50/p99 | T_release_lag_max p50/p99 (n) | T_addon_total SSE-incl p50 | JSON T_fw p90 | late p99 ms | loadgen busy max % | prov sched_err p99 ms | v1 JSON pre/post/overhead p50 ms | v1 SSE pre/guard p50 ms | busiest worker cores / median | worker RSS max MiB |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for run in sys.argv[1:]:
    R = E / run
    s = json.loads((R / "analysis/summary.json").read_text())
    res = json.loads((R / "sut_resources.json").read_text())
    ad = json.loads((R / "lg-v1aug/v1_adapter_report.json").read_text())
    vi = ad.get("v1_internal_ms_measure_phase", {})
    lcpu = s.get("loadgen_cpu") or [{}]
    wk = res.get("gateway_worker_cores") or {}
    print(f"| {run} | {f(g(s,'T_addon_first','p50'))} / {f(g(s,'T_addon_first','p99'))} | "
          f"{f(g(s,'T_release_lag_max','p50'))} / {f(g(s,'T_release_lag_max','p99'))} ({g(s,'T_release_lag_max','n')}) | "
          f"{f(g(s,'T_addon_total','p50'))} | {f(g(s,'T_fw_addon_json','p90'))} | {f(g(s,'schedule','lateness','p99'),3)} | "
          f"{f(max((x.get('busy_max') or 0) for x in lcpu),2)} | {f(g(s,'provider_sched_err_last','p99'),3)} | "
          f"{f(g(vi,'json','pre_ms','p50'))}/{f(g(vi,'json','post_ms','p50'))}/{f(g(vi,'json','overhead_ms','p50'))} | "
          f"{f(g(vi,'sse','pre_ms','p50'))}/{f(g(vi,'sse','post_ms','p50'))} | {f(wk.get('max'),2)} / {f(wk.get('p50'),3)} | "
          f"{f(g(res,'gateway_worker_rss_mib','max'),0)} |")
