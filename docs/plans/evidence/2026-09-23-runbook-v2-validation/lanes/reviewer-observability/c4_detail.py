#!/usr/bin/env python3
"""reviewer-observability: robustness of the client-side C4 view.
Per sampled qualified SSE stream: per-event excess x_k = d_k - d_0 (d_k = a[k] - e[m(k)], see c4_client.py);
where in the stream the max occurs (first 5 events / middle / last 2 events); per-event excess distribution;
bootstrap 95% CI of the C4 p99 (JSON total + SSE max(total, max d_k); infra = +inf) over the uniform 10% sample."""
import bisect, json, math, random, sys
from collections import Counter
from pathlib import Path
import orjson
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recompute as R
INF = float("inf")
def pct(xs, p):
    xs = sorted(xs); return xs[max(0, min(len(xs) - 1, math.ceil(p * len(xs)) - 1))]
root = Path(sys.argv[1]); direct = "--direct" in sys.argv
by_rid, calls, _, _ = R.load_provider(sorted(root.glob("*/prov")))
stages = R.STAGES_DEFAULT.split(",")
pos = Counter(); ev = []; c4 = []; nev = []; gaps_after_last = []
for d in sorted(root.glob("*/lg")):
    with R.open_any(R.find(d, "requests.jsonl")) as fh:
        for line in fh:
            c = orjson.loads(line)
            if c.get("ph") != 2 or R.fnv1a64(c["rid"]) % 10: continue
            p = by_rid.get(c["rid"]); st = R.stages_of(c.get("stages")); disp = (c.get("disp") or "").upper()
            if c.get("status") in R.BLOCK_STATUSES and disp == "BLOCK" and p is None: continue
            ok = (c.get("status") == 200 and c.get("done_seen") and not c.get("err") and p is not None
                  and calls.get(c["rid"]) == 1 and c.get("content_sha256") == p.get("content_sha256")
                  and (direct or (disp in R.QUAL_DISP and all(st.get(s) == "E" for s in stages))))
            if not ok: c4.append(INF); continue
            total = c["end_ns"] - p["recv_to_last_ns"]
            if not c.get("stream"): c4.append(total); continue
            e, P, a, C = p["emit_ns"], p["emit_cum"], c["arr_ns"], c["arr_cum"]
            ds = [a[k] - e[min(bisect.bisect_left(P, C[k]), len(P) - 1)] for k in range(len(a))]
            x = [v - ds[0] for v in ds]
            ev.extend(x[1:]); nev.append(len(ds))
            km = max(range(len(ds)), key=lambda i: ds[i])
            pos["first5" if km < 5 else ("last2" if km >= len(ds) - 2 else "middle")] += 1
            c4.append(max(total, max(ds)))
random.seed(1)
bs = sorted(pct([random.choice(c4) for _ in c4], 0.99) for _ in range(400))
f = lambda v: v if v == INF else round(v / 1e6, 3)
print(json.dumps({"run": root.name, "n_c4": len(c4), "C4_p99": f(pct(c4, 0.99)),
    "C4_p99_boot95": [f(bs[10]), f(bs[389])], "argmax_position": dict(pos),
    "events_per_stream_mean": round(sum(nev) / len(nev), 1) if nev else None,
    "per_event_excess_ms": {q: f(pct(ev, q)) for q in (0.5, 0.9, 0.99, 0.999)} if ev else None,
    "share_events_excess_gt_2ms": round(sum(1 for v in ev if v > 2e6) / len(ev), 4) if ev else None,
    "share_events_excess_gt_5ms": round(sum(1 for v in ev if v > 5e6) / len(ev), 4) if ev else None}))
