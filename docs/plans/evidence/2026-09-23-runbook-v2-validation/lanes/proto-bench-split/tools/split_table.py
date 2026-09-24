#!/usr/bin/env python3
"""split lane: one markdown table over all analysed steps (EVID/runs/<run>/split_*.json + harness analysis).

  split_table.py [RUN ...]    (default: every run with split_metrics.json, in creation order)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EVID = Path(__file__).resolve().parents[1]


def j(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def f(v: object, nd: int = 1) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and v == float("inf"):
        return "inf"
    if isinstance(v, (int, float)):
        return f"{v:.{nd}f}"
    return str(v)


def row(run: str) -> tuple[float, str] | None:
    d = EVID / "runs" / run
    m = j(d / "split_metrics.json")
    if not m:
        return None
    st = j(d / "step.json")
    su = (j(d / "split_sut.json").get("derived")) or {}
    h = j(d / "harness-analysis" / "summary.json")
    L = m.get("latency_ms", {})
    p = lambda k, q="p99": (L.get(k) or {}).get(q)  # noqa: E731
    guards = su.get("guards") or {}
    gpu = [g for gd in guards.values() for g in ((gd.get("gpu") or {}).values())]
    gpu_s = "/".join(f"{x['sm_mean']:.0f}({x['sm_max']:.0f})" for x in gpu) if gpu else "-"
    qmax = [((gd.get("owner_queue_gauges") or {}).get("o.guard_queue_windows.max") or {}).get("max") for gd in guards.values()]
    oq99 = [(((gd.get("owner_hist") or {}).get("guard_queue_ns")) or {}).get("p99_ms") for gd in guards.values()]
    bpr = [gd.get("gw_guard_bytes_per_req") for gd in guards.values()]
    ocpu = [gd.get("owner_cpu_ms_per_req") for gd in guards.values()]
    rtt = su.get("guard_rtt") or {}
    lg = (h.get("loadgen_cpu") or [{}])
    lgb = max((x.get("busy_max") or 0) for x in lg) if lg else None
    valid = h.get("valid") if h else None
    cells = [run, f(st.get("rate"), 0), st.get("mode", "?"), f(m.get("offered_rps")), f(m.get("qualified_rps")),
             f(p("JSON_total")), f(p("SSE_total")), f(p("SSE_first_tok1")), f(p("SSE_strict")),
             f(p("JSON_total", "p50")), f(p("SSE_total", "p50")),
             f(m.get("infra_pct"), 3), str(m.get("drops")), f(100 * (m.get("fp_block_rate") or 0), 2),
             f(su.get("gw_cpu_ms_per_req"), 2), "/".join(f(x, 2) for x in ocpu) or "-",
             f(((su.get("gw_core_util_schedstat") or {}).get("sum_cores")), 2), gpu_s,
             f(rtt.get("p50_ms"), 2), f(rtt.get("p99_ms"), 2), "/".join(f(x, 0) for x in qmax) or "-",
             "/".join(f(x, 2) for x in oq99) or "-", "/".join(f(x, 0) for x in bpr) or "-",
             f(lgb), str(valid), m.get("verdict", "?")]
    return (st.get("created_utc") or run, "| " + " | ".join(cells) + " |")


HDR = ("| run | rate | mode | offered/s | qualified/s | p99 JSON | p99 SSE-total | p99 SSE-first | p99 SSE-strict | "
       "p50 JSON | p50 SSE-total | infra % | drops | FP-block % | gw CPU-ms/req | owner CPU-ms/req | gw cores | "
       "GPU sm% mean(max) | RTT p50 | RTT p99 | owner queue max (windows) | owner queue p99 ms | gw<->guard B/req | "
       "lg busy max % | valid | verdict |")


def main() -> int:
    runs = sys.argv[1:] or [p.name for p in (EVID / "runs").iterdir() if (p / "split_metrics.json").exists()]
    rows = sorted(r for r in (row(x) for x in runs) if r)
    print(HDR)
    print("|" + "---|" * HDR.count(" | ") + "---|")
    for _, line in rows:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
