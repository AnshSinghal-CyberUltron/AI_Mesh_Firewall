#!/usr/bin/env python3
"""SSE buffering check. Joins olg per-chunk ARRIVALS (arr_ns, arr_cum; client monotonic clock, from send start) with
synthprov per-chunk EMISSIONS (emit_ns, emit_cum; provider clock, from body read) on request id. No cross-host clock
math: both sides are taken relative to their own FIRST content chunk.
  hold_k   = (arr_k - arr_first) - (emit_j - emit_first), j = last provider chunk fully covered by arr_cum[k]
             -> extra delay a chunk suffered in transit beyond the provider's own pacing (buffering shows up here)
  gaps     = client inter-arrival gaps between consecutive content arrivals (unbuffered ~= provider ITL 20 ms)
  chunks/arrival = provider chunks delivered per client read (1.0 = every token delivered separately)
usage: sse_analyze.py OLG_REQUESTS(.jsonl|.zst) SYNTH_RECORDS.jsonl"""
import json, subprocess, sys
import numpy as np
def rows(p):
    f = subprocess.Popen(["zstd", "-dcq", p], stdout=subprocess.PIPE).stdout if p.endswith(".zst") else open(p, "rb")
    for l in f:
        if l.strip(): yield json.loads(l)
prov = {r["rid"]: r for r in rows(sys.argv[2]) if r.get("emit_ns")}
holds, gaps, maxhold, cpa, ttft_c, done, n, errs, ctypes = [], [], [], [], [], 0, 0, 0, {}
for r in rows(sys.argv[1]):
    if r.get("ph", 2) != 2 or not r.get("stream"): continue
    n += 1
    if r.get("err"): errs += 1; continue
    done += bool(r.get("done_seen")); ctypes[r.get("ctype")] = ctypes.get(r.get("ctype"), 0) + 1
    p = prov.get(r["rid"]); a, ac = r.get("arr_ns") or [], r.get("arr_cum") or []
    if not p or not a: continue
    e, ec = p["emit_ns"], p["emit_cum"]
    ttft_c.append(a[0] / 1e6)
    j = 0; mh = 0.0
    for k in range(len(a)):
        while j + 1 < len(ec) and ec[j + 1] <= ac[k]: j += 1
        h = ((a[k] - a[0]) - (e[j] - e[0])) / 1e6; holds.append(h); mh = max(mh, h)
        if k: gaps.append((a[k] - a[k - 1]) / 1e6)
    maxhold.append(mh); cpa.append(len(e) / len(a))
P = lambda x, q: round(float(np.percentile(x, q)), 3) if len(x) else None
print(json.dumps({"streams_measured": n, "errors": errs, "done_seen": done, "joined": len(maxhold), "content_types": ctypes,
    "provider_chunks_per_client_arrival": {"mean": round(float(np.mean(cpa)), 3) if cpa else None, "max": round(float(np.max(cpa)), 2) if cpa else None},
    "gap_ms": {q: P(gaps, q) for q in (1, 5, 50, 95, 99)},
    "gap_lt_2ms_frac": round(float(np.mean(np.asarray(gaps) < 2.0)), 4) if gaps else None,
    "hold_ms": {q: P(holds, q) for q in (50, 99, 99.9)} | {"max": round(float(np.max(holds)), 3) if holds else None},
    "max_hold_per_stream_ms": {q: P(maxhold, q) for q in (50, 99)} | {"max": round(float(np.max(maxhold)), 3) if maxhold else None},
    "client_first_content_ms": {q: P(ttft_c, q) for q in (50, 99)}}))
