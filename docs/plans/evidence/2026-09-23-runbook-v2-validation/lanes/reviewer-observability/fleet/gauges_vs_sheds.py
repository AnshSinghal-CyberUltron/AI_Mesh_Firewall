#!/usr/bin/env python3
"""During each run's measurement window: live gauges the sampler recorded (every ~5 s, from the workers' 1 s
metric dumps) vs the shed rate the clients saw. usage: gauges_vs_sheds.py RUN..."""
import json, sys
from collections import Counter
from pathlib import Path
import orjson
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recompute as R  # noqa: E402
RAW = Path.home() / "rv-evidence-raw/proto-bench-fleet/runs"
for run in sys.argv[1:]:
    rd = RAW / run
    step = json.loads((rd / "step.json").read_text())
    g = Counter(); gmax = {}; ns = 0
    for u in step["units"].split():
        s = rd / u / "sut"; sch = json.loads((s / "schedule.json").read_text())
        with R.open_any(R.find(s, "samples.jsonl")) as fh:
            for line in fh:
                d = orjson.loads(line)
                if "gauges" not in d or not (sch["meas_start"] <= d["t"] <= sch["meas_end"]):
                    continue
                ns += 1
                for k, v in d["gauges"].items():
                    if k.endswith(".max"):
                        gmax.setdefault(k, []).append(v)
    n = s503 = 0
    for d in sorted(rd.glob("rv-pbf-lg-*/lg")):
        with R.open_any(R.find(d, "requests.jsonl")) as fh:
            for line in fh:
                c = orjson.loads(line)
                if c.get("ph") == 2:
                    n += 1; s503 += c.get("status") == 503
    summ = {k: {"median": sorted(v)[len(v) // 2], "max": max(v), "share_zero": round(sum(1 for x in v if x == 0) / len(v), 2)}
            for k, v in sorted(gmax.items()) if k.split(".")[1] in ("guard_queue_items", "guard_inflight_tokens", "input_queue_depth", "inflight_requests")}
    print(json.dumps({"run": run, "client_503_pct": round(100 * s503 / n, 3), "gauge_samples": ns, "gauges": summ}))
