#!/usr/bin/env python3
"""reviewer-observability: recompute SUT CPU-ms per offered request from the unit sampler's per-process
schedstat run_ns (all threads) over the measurement window [meas_start, meas_end] (schedule.json)."""
import json, sys
from pathlib import Path
from compression import zstd
for r in sys.argv[1:]:
    U = Path.home() / "rv-evidence-raw/proto-bench-unit/runs" / r / "rv-proto-unit-1/unit"
    sch = json.loads((U / "schedule.json").read_text())
    samples = [json.loads(l) for l in zstd.open(U / "samples.jsonl.zst", "rb")]
    a = min(samples, key=lambda s: abs(s["t"] - sch["meas_start"])); b = min(samples, key=lambda s: abs(s["t"] - sch["meas_end"]))
    by_role = {}
    for pid, pb in b["procs"].items():
        pa = a["procs"].get(pid)
        if pa is None: continue
        role = pb["role"].rstrip("0123456789") if pb["role"].startswith("owner") else pb["role"]
        by_role[role] = by_role.get(role, 0) + pb["run_ns"] - pa["run_ns"]
    win = b["t"] - a["t"]
    rate = json.loads((U.parent.parent / "step.json").read_text())["rate"]
    n = rate * 300
    gw = sum(v for k, v in by_role.items() if k in ("worker", "owner", "launcher"))
    print(json.dumps({"run": r, "window_s": round(win, 2), "offered_in_window": n,
                      "cores_by_role": {k: round(v / 1e9 / win, 3) for k, v in by_role.items()},
                      "gateway_cpu_ms_per_req": round(gw / 1e6 / (n * win / 300), 3)}))
