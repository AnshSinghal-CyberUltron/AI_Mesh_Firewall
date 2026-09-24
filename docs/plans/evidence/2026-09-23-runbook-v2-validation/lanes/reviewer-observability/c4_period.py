#!/usr/bin/env python3
"""reviewer-observability: is the mid-stream extra delay periodic at 1 s (rvproto's per-worker metrics dump,
edge/app.py _dump_metrics: sleep(1.0) + json dump on the event loop) or aperiodic (tokenization etc.)?
For each sampled qualified SSE stream (one stream = one worker), take events with excess > THR ms; for every
pair of such events in the same stream, phase = (t_j - t_i) mod 1.0 s using provider emission times of the
triggering pieces. A 1 s periodic source concentrates phases near 0 / 1 s; an aperiodic one is uniform."""
import bisect, json, math, sys
from collections import Counter
from pathlib import Path
import orjson
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recompute as R
root = Path(sys.argv[1]); thr = float(sys.argv[2]) * 1e6 if len(sys.argv) > 2 else 2e6
by_rid, calls, _, _ = R.load_provider(sorted(root.glob("*/prov")))
bins = Counter(); npairs = 0; nstreams = 0; ndelayed = 0; total_events = 0
for d in sorted(root.glob("*/lg")):
    with R.open_any(R.find(d, "requests.jsonl")) as fh:
        for line in fh:
            c = orjson.loads(line)
            if c.get("ph") != 2 or not c.get("stream") or not c.get("sampled") or c.get("status") != 200: continue
            p = by_rid.get(c["rid"])
            if not p or not p.get("emit_ns") or c.get("content_sha256") != p.get("content_sha256"): continue
            e, P, a, C = p["emit_ns"], p["emit_cum"], c["arr_ns"], c["arr_cum"]
            ms = [min(bisect.bisect_left(P, C[k]), len(P) - 1) for k in range(len(a))]
            ds = [a[k] - e[ms[k]] for k in range(len(a))]
            base = sorted(ds)[len(ds) // 2]            # the stream's typical release addon
            t = [e[ms[k]] for k in range(len(a)) if ds[k] - base > thr]
            nstreams += 1; total_events += len(a); ndelayed += len(t)
            for i in range(len(t)):
                for j in range(i + 1, len(t)):
                    dt = t[j] - t[i]
                    if dt < 0.2e9: continue          # same stall / adjacent events
                    ph = (dt % 1e9) / 1e9
                    ph = min(ph, 1 - ph)             # distance to nearest whole second, 0..0.5
                    bins[int(ph * 20)] += 1          # 25 ms bins
                    npairs += 1
tot = sum(bins.values()) or 1
print(json.dumps({"run": root.name, "thr_ms": thr / 1e6, "streams": nstreams, "events": total_events,
                  "delayed_events": ndelayed, "delayed_share": round(ndelayed / max(total_events, 1), 4), "pairs": npairs,
                  "phase_hist_25ms_bins_(dist_to_whole_second)": [round(bins[i] / tot, 3) for i in range(10)],
                  "uniform_expectation_per_bin": 0.1}))
