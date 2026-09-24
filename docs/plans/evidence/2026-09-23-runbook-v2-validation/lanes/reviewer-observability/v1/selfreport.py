#!/usr/bin/env python3
"""v1 self-reported timing (its own audit event: pipeline_trace overhead_ms, t_addon_pre/post) vs the wire
(client T_fw_addon per request, harness definition, same request joined strictly by nonce + client_corr).
Measurement phase, client-OK requests only. usage: selfreport.py RUN_DIR..."""
import json, math, re, sys
from collections import defaultdict
from pathlib import Path
import orjson
NONCE_RE = re.compile(r"\(ref-[a-z]+(?:-[a-z]+){5}\)")
def open_any(p):
    s = str(p)
    if s.endswith(".zst"):
        from compression import zstd
        return zstd.open(s, "rb")
    return open(s, "rb")
def pct(xs, p):
    xs = sorted(xs)
    return xs[max(0, min(len(xs) - 1, math.ceil(p * len(xs)) - 1))] if xs else None
def fl(x):
    try: return float(x)
    except (TypeError, ValueError): return None
for arg in sys.argv[1:]:
    R = Path(arg); lgd = next(R.glob("rv-v1-lg-*/lg")); pvd = next(R.glob("rv-v1-prov-*/prov"))
    f = lgd / "requests.jsonl.zst"
    if not f.exists(): f = lgd / "requests.jsonl"
    pf = pvd / "records.jsonl.zst"
    if not pf.exists(): pf = pvd / "records.jsonl"
    prov = {}
    for l in open_any(pf):
        if l.strip():
            r = orjson.loads(l); prov[r["rid"]] = r
    ev = defaultdict(list)
    for l in open_any(R / "sut" / "v1-events.jsonl.zst"):
        if l.strip():
            e = orjson.loads(l); m = NONCE_RE.search(e.get("inp") or "")
            if m: ev[m.group(0)].append(e)
    acc = defaultdict(lambda: defaultdict(list))
    for l in open_any(f):
        if not l.strip(): continue
        c = orjson.loads(l)
        if c.get("ph") != 2 or not (c.get("status") == 200 and not c.get("err") and c.get("done_seen")): continue
        p = prov.get(c["rid"])
        es = [e for e in ev.get(c.get("nonce"), []) if e.get("client_corr") in (None, "", c["rid"])]
        if p is None or not es: continue
        e = es[0]
        k = "sse" if c.get("stream") else "json"
        tot = c["end_ns"] - p["recv_to_last_ns"]
        fw = max(tot, c["first_ns"] - p["recv_to_first_ns"]) if k == "sse" else tot
        acc[k]["client_T_fw_addon_ms"].append(fw / 1e6)
        acc[k]["client_T_addon_total_ms"].append(tot / 1e6)
        for fld in ("overhead_ms", "pre_ms", "post_ms"):
            v = fl(e.get(fld))
            if v is not None: acc[k]["v1_" + fld].append(v)
    out = {"run": R.name}
    for k, d in acc.items():
        out[k] = {m: {"n": len(v), "p50": round(pct(v, .5), 1), "p99": round(pct(v, .99), 1)} for m, v in d.items() if v}
        if d.get("v1_overhead_ms"):
            out[k]["v1_overhead_zero_share"] = round(sum(1 for x in d["v1_overhead_ms"] if x == 0) / len(d["v1_overhead_ms"]), 4)
    print(json.dumps(out))
