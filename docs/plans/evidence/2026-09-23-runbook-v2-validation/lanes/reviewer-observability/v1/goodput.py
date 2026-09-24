#!/usr/bin/env python3
"""v1 goodput: qualified requests per second two ways.
 cohort : requests SCHEDULED in the measurement window that ended 200+[DONE]/JSON (what the lane reports / 300 s)
 completion: OK completions whose completion instant (sched+late+end, loadgen clock) falls inside the window,
             per 30 s bin -> steady-state and trend.  usage: goodput.py RUN_DIR..."""
import json, sys
from collections import Counter
from pathlib import Path
import orjson
def open_any(p):
    s = str(p)
    if s.endswith(".zst"):
        from compression import zstd
        return zstd.open(s, "rb")
    return open(s, "rb")
for arg in sys.argv[1:]:
    R = Path(arg); lgd = next(R.glob("rv-v1-lg-*/lg"))
    cfg = json.loads((lgd / "manifest.json").read_text())["config"]
    w0 = cfg["ramp_s"] + cfg["warmup_s"]; w1 = w0 + cfg["duration_s"]
    f = lgd / "requests.jsonl.zst"
    if not f.exists(): f = lgd / "requests.jsonl"
    coh = 0; comp = Counter(); errs = Counter()
    for l in open_any(f):
        if not l.strip(): continue
        c = orjson.loads(l)
        ok = c.get("status") == 200 and not c.get("err") and c.get("done_seen")
        if c.get("ph") == 2 and ok: coh += 1
        t = (c.get("sched_ns", 0) + c.get("late_ns", 0) + (c.get("end_ns") or 0)) / 1e9
        if w0 <= t < w1:
            (comp if ok else errs)[int((t - w0) // 30)] += 1
    bins = [comp[i] / 30 for i in range(int(cfg["duration_s"] // 30))]
    print(json.dumps({"run": R.name, "rate": cfg["rate"], "cohort_qualified_per_s": round(coh / cfg["duration_s"], 2),
                      "completion_ok_per_s_window_mean": round(sum(comp.values()) / cfg["duration_s"], 2),
                      "completion_ok_per_s_by_30s": [round(x, 1) for x in bins],
                      "completion_err_per_s_by_30s": [round(errs[i] / 30, 1) for i in range(len(bins))]}))
