#!/usr/bin/env python3
"""reviewer-observability: CPU cost of rvproto's own metrics exposition, measured with the frozen rvproto code
(runtime/metrics.py) on a REAL worker export from a bench run (so histogram bucket counts are realistic).
These calls run synchronously on the worker's event loop: /metrics -> prometheus(export()) ; RV_METRICS_DIR dump
-> json.dumps(export()) + write/rename every 1 s ; /metrics/all -> merge + summarize."""
import json, sys, time, tempfile, os
from pathlib import Path
sys.path.insert(0, sys.argv[1])            # SP/rvproto
from rvproto.runtime import metrics as M
snap = json.loads(Path(sys.argv[2]).read_text())
reg = M.Registry(0)
for name, h in snap["hist"].items():
    hh = reg.h(name)
    for k, c in h["b"].items():
        hh.counts[int(k)] = c
    hh.n, hh.total, hh.vmax, hh.vmin = h["n"], h["sum"], h["max"], h.get("min", 0)
reg.count.update(snap["count"]); reg.gauge.update(snap["gauge"])
def t(f, n):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter(); f(); ts.append((time.perf_counter() - t0) * 1e3)
    ts.sort(); return {"n": n, "p50_ms": round(ts[n // 2], 3), "max_ms": round(ts[-1], 3)}
d = tempfile.mkdtemp()
print("histograms", len(reg.hist), "export_json_bytes", len(json.dumps(reg.export())))
print("prometheus(export()) [/metrics scrape]", t(lambda: M.prometheus(reg.export()), 30))
print("dump() [RV_METRICS_DIR, every 1 s]   ", t(lambda: reg.dump(d), 100))
ex = [reg.export() for _ in range(18)]
print("merge(18)+summarize [/metrics/all]   ", t(lambda: M.summarize(M.merge(ex)["hist"]), 10))
