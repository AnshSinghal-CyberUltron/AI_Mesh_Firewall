#!/usr/bin/env python3
"""reviewer-observability: bound the part of the client-side C4 excess caused by rvproto's 1 s metrics dump
(bench-only code path). Per sampled qualified SSE stream: delayed events D = {k : d_k - median(d) > 2 ms};
events whose trigger times are 1 s-periodic with another delayed event (spacing within +-WIN ms of a whole
number of seconds >= 1 s) are 'dump-aligned' and removed, together with every event predicted to fall in
the same periodic window. C4' = max(T_addon_total, max over remaining d_k). Streams with a single delayed
event keep it (so C4' is still an OVER-estimate of a dump-free run)."""
import bisect, json, math, sys
from pathlib import Path
import orjson
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recompute as R
INF = float("inf")
def pct(xs, p):
    xs = sorted(xs); return xs[max(0, min(len(xs) - 1, math.ceil(p * len(xs)) - 1))]
root = Path(sys.argv[1]); WIN = float(sys.argv[2]) * 1e6 if len(sys.argv) > 2 else 30e6
by_rid, calls, _, _ = R.load_provider(sorted(root.glob("*/prov")))
stages = R.STAGES_DEFAULT.split(",")
c4, c4n, removed_share = [], [], []
for d in sorted(root.glob("*/lg")):
    with R.open_any(R.find(d, "requests.jsonl")) as fh:
        for line in fh:
            c = orjson.loads(line)
            if c.get("ph") != 2 or R.fnv1a64(c["rid"]) % 10: continue
            p = by_rid.get(c["rid"]); st = R.stages_of(c.get("stages")); disp = (c.get("disp") or "").upper()
            if c.get("status") in R.BLOCK_STATUSES and disp == "BLOCK" and p is None: continue
            ok = (c.get("status") == 200 and c.get("done_seen") and not c.get("err") and p is not None
                  and calls.get(c["rid"]) == 1 and c.get("content_sha256") == p.get("content_sha256")
                  and disp in R.QUAL_DISP and all(st.get(s) == "E" for s in stages))
            if not ok: c4.append(INF); c4n.append(INF); continue
            total = c["end_ns"] - p["recv_to_last_ns"]
            if not c.get("stream"): c4.append(total); c4n.append(total); continue
            e, P, a, C = p["emit_ns"], p["emit_cum"], c["arr_ns"], c["arr_cum"]
            ms = [min(bisect.bisect_left(P, C[k]), len(P) - 1) for k in range(len(a))]
            ds = [a[k] - e[ms[k]] for k in range(len(a))]
            t = [e[m] for m in ms]
            med = sorted(ds)[len(ds) // 2]
            D = [k for k in range(len(ds)) if ds[k] - med > 2e6]
            anchors = set()
            for i in range(len(D)):
                for j in range(i + 1, len(D)):
                    dt = t[D[j]] - t[D[i]]
                    if dt < 0.9e9: continue
                    r = dt % 1e9
                    if min(r, 1e9 - r) <= WIN:
                        anchors.add(D[i]); anchors.add(D[j])
            drop = set()
            for ai in anchors:
                for k in range(len(ds)):
                    r = (t[k] - t[ai]) % 1e9
                    if min(r, 1e9 - r) <= WIN:
                        drop.add(k)
            keep = [ds[k] for k in range(len(ds)) if k not in drop] or [ds[0]]
            c4.append(max(total, max(ds))); c4n.append(max(total, max(keep)))
            removed_share.append(len(drop) / len(ds))
f = lambda v: v if v == INF else round(v / 1e6, 3)
import random
random.seed(1)
def boot(xs):
    bs = sorted(pct([random.choice(xs) for _ in xs], 0.99) for _ in range(400))
    return [f(bs[10]), f(bs[389])]
print(json.dumps({"run": root.name, "win_ms": WIN / 1e6, "n": len(c4), "C4_p50_p90_p99": [f(pct(c4, q)) for q in (0.5, 0.9, 0.99)],
                  "C4_p99_boot95": boot(c4),
                  "C4_nodump_p50_p90_p99": [f(pct(c4n, q)) for q in (0.5, 0.9, 0.99)],
                  "C4_nodump_p99_boot95": boot(c4n),
                  "events_removed_mean_share": round(sum(removed_share) / max(len(removed_share), 1), 4)}))
