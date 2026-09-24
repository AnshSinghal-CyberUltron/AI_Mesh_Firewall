#!/usr/bin/env python3
"""binding.py RUN [RUN ...]: what binds, per run (from EVID/runs/<run>/split_sut.json + the raw loadgen manifests).
Columns: per-guard owner req/s, owner busy (req/s x mean exec), GPU sm% mean, owner queue p99 (worst guard), guard RTT
p99 / tokenize p99 / t_input p99 / loop_lag p99 (worst gateway), cores busy per gateway (mean of gateways, of 16 vCPU),
worker util max, edge + redis VM cores, loadgen CPU busy % (mean of LGs), C4 p99, verdict."""
import glob
import json
import sys
from pathlib import Path

EVID = Path(__file__).resolve().parents[1]
RAW = Path("/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs")


def j(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {}


def worst(gws, key, q="p99_ms"):
    vals = [(g.get(key) or {}).get(q) for g in gws.values()]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def f(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


print("| run | guards x owner req/s | owner busy | GPU sm% mean | owner queue p99 ms | guard exec p99 ms | RTT p99 | tokenize p99 | t_input p99 | "
      "loop_lag p99 | cores/gateway (of 16) | worker util max | edge cores | redis cores | LG CPU % | C4 p99 | verdict |")
print("|" + "---|" * 17)
for run in sys.argv[1:]:
    d = (j(EVID / "runs" / run / "split_sut.json").get("derived")) or {}
    v = j(EVID / "runs" / run / "verdict.json")
    G, W = d.get("guards") or {}, d.get("gateways") or {}
    rps, busy, sm, qp99, xp99 = [], [], [], [], []
    for gd in G.values():
        n = (gd.get("owner_counts") or {}).get("owner_requests", 0) / 300.0
        h = gd.get("owner_hist") or {}
        rps.append(n)
        busy.append(n * (h.get("guard_exec_ns") or {}).get("mean_ms", 0) / 1000)
        qp99.append((h.get("guard_queue_ns") or {}).get("p99_ms", 0))
        xp99.append((h.get("guard_exec_ns") or {}).get("p99_ms", 0))
        sm += [x["sm_mean"] for x in (gd.get("gpu") or {}).values()]
    gcores = [g.get("vm_cores_busy") for g in W.values() if g.get("vm_cores_busy") is not None]
    wu = [(g.get("worker_util") or {}).get("max") for g in W.values()]
    wu = [x for x in wu if x is not None]
    ex = d.get("extras") or {}
    edge = next((x.get("vm_cores_busy") for k, x in ex.items() if "edge" in k), None)
    red = next((x.get("vm_cores_busy") for k, x in ex.items() if "redis" in k), None)
    lgs = [j(p).get("cpu_measure_phase", {}).get("busy_mean") for p in glob.glob(str(RAW / run / "rv-split-lg-*" / "lg" / "manifest.json"))]
    lgs = [x for x in lgs if x is not None]
    cells = [run, f"{len(rps)} x {sum(rps) / len(rps):.0f}" if rps else "-", f(sum(busy) / len(busy), 2) if busy else "-",
             f(sum(sm) / len(sm), 0) if sm else "-", f(max(qp99), 1) if qp99 else "-", f(max(xp99), 1) if xp99 else "-",
             f(worst(W, "guard_rtt")), f(worst(W, "t_tokenize")), f(worst(W, "t_input")), f(worst(W, "loop_lag")),
             f(sum(gcores) / len(gcores), 2) if gcores else "-", f(max(wu), 2) if wu else "-", f(edge, 2), f(red, 2),
             f(sum(lgs) / len(lgs), 0) if lgs else "-", f(v.get("c4_p99")), v.get("verdict", "?")]
    print("| " + " | ".join(cells) + " |")
