#!/usr/bin/env python3
"""split lane report table (C4 rule): one row per run from EVID/runs/<run>/{step,verdict,split_metrics,c4,split_sut}.json.

  split_table2.py RUN [RUN ...]
Columns: topology, arrivals, rate, qualified/s, C4 p99 (all/SSE/JSON) + p99.9, load-rule p99 JSON / SSE-total /
SSE-first, infra %, gateway CPU-ms/request (all gateways), gateway cores busy, t_input p99/p99.9 and loop_lag
p99/p99.9 (worst gateway), guard RTT p50/p99/p99.9 (worst gateway), per-guard GPU sm% mean (max), owner
CPU-ms/request, guard VM cores busy, verdict.
"""
import json
import sys
from pathlib import Path

EVID = Path(__file__).resolve().parents[1]


def j(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def f(v, nd=1):
    if v is None:
        return "-"
    if v == float("inf"):
        return "inf"
    return f"{v:.{nd}f}"


def topo(st: dict) -> str:
    if st.get("mode") == "direct":
        return "DIRECT"
    g = len((st.get("gws") or st.get("gw") or "").split())
    k = len((st.get("guards") or "").split())
    edge = "+edge" if ":8080" in (st.get("targets") or "") else ""
    return f"{g}gw/{k}g{edge}"


def row(run: str) -> str:
    d = EVID / "runs" / run
    st, v, m, c = j(d / "step.json"), j(d / "verdict.json"), j(d / "split_metrics.json"), j(d / "c4.json")
    su = (j(d / "split_sut.json").get("derived")) or {}
    L = m.get("latency_ms", {})
    arr = "poisson" if "poisson" in st.get("olg_flags", "") else ("direct" if st.get("mode") == "direct" else "const")
    if "worst" in st.get("corpus", ""):
        arr += " WORST"
    build = "li ON" if "li-knobs" in st.get("build", "") else "frozen"
    g = su.get("guards") or {}
    gpu = "/".join(f"{x['sm_mean']:.0f}({x['sm_max']:.0f})" for gd in g.values() for x in (gd.get("gpu") or {}).values()) or "-"
    ocpu = [gd.get("owner_cpu_ms_per_req") for gd in g.values() if gd.get("owner_cpu_ms_per_req") is not None]
    gvm = [gd.get("vm_cores_busy") for gd in g.values() if gd.get("vm_cores_busy") is not None]
    ti, ll, rt = su.get("t_input") or {}, su.get("loop_lag") or {}, su.get("guard_rtt") or {}
    c4 = c.get("C4_T_fw_addon_ABC", {})
    gwc = su.get("gw_cores_busy_total")
    if gwc is None:
        gwc = (su.get("gw_core_util_schedstat") or {}).get("sum_cores")
    cells = [run, topo(st), build, arr, str(st.get("rate")), f(m.get("qualified_rps")),
             f"{f(c4.get('p99'))}/{f((c.get('C4_sse') or {}).get('p99'))}/{f((c.get('C4_json') or {}).get('p99'))}", f(c4.get("p999")),
             f((c.get("proc_extra_per_stream") or {}).get("p99")),
             f((L.get("JSON_total") or {}).get("p99")), f((L.get("SSE_total") or {}).get("p99")), f((L.get("SSE_first_tok1") or {}).get("p99")),
             f(m.get("infra_pct"), 3), f(su.get("gw_cpu_ms_per_req"), 2), f(gwc, 2),
             f"{f(ti.get('p99_ms'))}/{f(ti.get('p99.9_ms'))}", f"{f(ll.get('p99_ms'))}/{f(ll.get('p99.9_ms'))}",
             f"{f(rt.get('p50_ms'), 2)}/{f(rt.get('p99_ms'), 2)}/{f(rt.get('p99.9_ms'), 2)}", gpu,
             f(sum(ocpu) / len(ocpu), 2) if ocpu else "-", f(sum(gvm), 2) if gvm else "-", v.get("verdict", "?")]
    return "| " + " | ".join(cells) + " |"


HDR = ("| run | topology | build | arrivals | rate | qual/s | C4 p99 all/SSE/JSON | C4 p99.9 | mid-stream extra p99 | p99 JSON | p99 SSE-total | p99 SSE-first | "
       "infra % | gw CPU-ms/req | gw cores | t_input p99/p99.9 | loop_lag p99/p99.9 | RTT p50/p99/p99.9 | GPU sm% per guard | "
       "owner CPU-ms/req (per guard) | guard VM cores (sum) | verdict |")


def main() -> int:
    print(HDR)
    print("|" + "---|" * (HDR.count("|") - 1))
    for r in sys.argv[1:]:
        print(row(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
