#!/usr/bin/env python3
"""reviewer-observability / v1: does each v1 audit event describe ONE request consistently?
For every exported event: client record via client_corr (x-request-id) vs client record via the nonce in the
event's input text. Mismatch = the event's correlation id and its content belong to different requests.
Also counts events per client rid (duplicates) and per nonce. usage: attrib_check.py RUN_DIR"""
import json, re, sys
from collections import Counter
from pathlib import Path
import orjson
NONCE_RE = re.compile(r"\(ref-[a-z]+(?:-[a-z]+){5}\)")
def open_any(p):
    s = str(p)
    if s.endswith(".zst"):
        from compression import zstd
        return zstd.open(s, "rb")
    return open(s, "rb")
R = Path(sys.argv[1])
lgd = next(R.glob("rv-v1-lg-*/lg"))
f = lgd / "requests.jsonl.zst"
if not f.exists(): f = lgd / "requests.jsonl"
by_rid, by_nonce = {}, {}
for line in open_any(f):
    if line.strip():
        c = orjson.loads(line)
        by_rid[c["rid"]] = c
        by_nonce[c.get("nonce")] = c
ev = [orjson.loads(l) for l in open_any(R / "sut" / "v1-events.jsonl.zst") if l.strip()]
stat = Counter(); per_rid = Counter(); per_nonce = Counter(); ex = []
for e in ev:
    m = NONCE_RE.search(e.get("inp") or "")
    n = m.group(0) if m else None
    cr = e.get("client_corr")
    a = by_rid.get(cr) if cr else None
    b = by_nonce.get(n) if n else None
    per_rid[cr] += 1
    per_nonce[n] += 1
    if a is None and b is None: k = "no_client_match"
    elif a is None: k = "nonce_only"
    elif b is None: k = "corr_only"
    elif a is b: k = "consistent"
    else:
        k = "MISMATCH_corr_vs_content"
        if len(ex) < 5:
            ex.append({"event_id": e["id"], "et": e.get("et"), "client_corr": cr, "corr_client_nonce": a.get("nonce"),
                       "content_nonce": n, "content_client_rid": b["rid"], "corr_client_status": a.get("status"),
                       "content_client_status": b.get("status"), "completed": e.get("completed")})
    stat[(k, e.get("et"))] += 1
dup_rid = sum(1 for k, v in per_rid.items() if k and v > 1)
dup_nonce = sum(1 for k, v in per_nonce.items() if k and v > 1)
print(json.dumps({"run": R.name, "events": len(ev), "by_kind": {f"{a}|{b}": v for (a, b), v in stat.most_common()},
                  "client_corr_with_multiple_events": dup_rid, "extra_events_same_corr": sum(v - 1 for k, v in per_rid.items() if k and v > 1),
                  "nonces_with_multiple_events": dup_nonce, "examples": ex}, indent=1))
