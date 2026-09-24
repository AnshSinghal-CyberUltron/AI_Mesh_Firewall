#!/usr/bin/env python3
"""For audit events whose client_corr and content nonce point to different client requests: which request does
the event's canonical v1 request_id (metadata.request_id, 'zs-...') belong to? Client side: olg resp_rid = the
x-request-id response header v1 sent back ('=' means echoed; else v1's zs- id). Also client outcomes of both.
usage: attrib_rid.py RUN_DIR"""
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
by_rid, by_nonce, by_zs = {}, {}, {}
for line in open_any(f):
    if line.strip():
        c = orjson.loads(line)
        by_rid[c["rid"]] = c; by_nonce[c.get("nonce")] = c
        if c.get("resp_rid") and c["resp_rid"] != "=":
            by_zs[c["resp_rid"]] = c
def outc(c):
    if c is None: return None
    if c.get("status") == 200 and not c.get("err") and c.get("done_seen"): return "ok"
    return c.get("err") or f"http_{c.get('status')}"
res = Counter(); oc = Counter()
for l in open_any(R / "sut" / "v1-events.jsonl.zst"):
    if not l.strip(): continue
    e = orjson.loads(l)
    m = NONCE_RE.search(e.get("inp") or ""); n = m.group(0) if m else None
    a = by_rid.get(e.get("client_corr")); b = by_nonce.get(n)
    if a is None or b is None or a is b: continue
    z = by_zs.get(e.get("rid"))
    res["zs_rid_is_corr_request" if z is a else "zs_rid_is_content_request" if z is b else "zs_rid_neither" if z else "zs_rid_unknown_to_client"] += 1
    oc[(outc(a), outc(b))] += 1
print(json.dumps({"run": R.name, "mismatched_events_canonical_rid": dict(res),
                  "client_outcomes(corr_request, content_request)": {f"{k[0]}|{k[1]}": v for k, v in oc.most_common()}}, indent=1))
