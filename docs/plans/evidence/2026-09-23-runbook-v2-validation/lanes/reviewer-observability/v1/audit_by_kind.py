#!/usr/bin/env python3
"""v1: strict audit coverage of DISPATCHED requests (provider recorded the call) by stream/json x client outcome,
and provider completion (tokens_out == max_tokens_req, client_gone). usage: audit_by_kind.py RUN_DIR"""
import json, re, sys
from collections import Counter, defaultdict
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
lgd = next(R.glob("rv-v1-lg-*/lg")); pvd = next(R.glob("rv-v1-prov-*/prov"))
f = lgd / "requests.jsonl.zst"
if not f.exists(): f = lgd / "requests.jsonl"
pf = pvd / "records.jsonl.zst"
if not pf.exists(): pf = pvd / "records.jsonl"
prov = {}
for l in open_any(pf):
    if l.strip():
        r = orjson.loads(l); prov[r["rid"]] = r
by_nonce = defaultdict(list)
for l in open_any(R / "sut" / "v1-events.jsonl.zst"):
    if l.strip():
        e = orjson.loads(l); m = NONCE_RE.search(e.get("inp") or "")
        if m: by_nonce[m.group(0)].append(e)
t = Counter()
for l in open_any(f):
    if not l.strip(): continue
    c = orjson.loads(l)
    p = prov.get(c["rid"])
    if p is None: continue
    ok = c.get("status") == 200 and not c.get("err") and c.get("done_seen")
    strict = [e for e in by_nonce.get(c.get("nonce"), []) if e.get("client_corr") in (None, "", c["rid"])]
    full = p.get("tokens_out") == p.get("max_tokens_req")
    t[("sse" if c.get("stream") else "json", "client_ok" if ok else f"client_{c.get('err') or c.get('status')}",
       "audited" if strict else "NOT_audited", "prov_full" if full else "prov_partial", "gone" if p.get("client_gone") else "")] += 1
print(R.name)
for k, v in sorted(t.items()): print("  ", v, k)
