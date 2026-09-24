#!/usr/bin/env python3
"""reviewer-observability / v1: lane-style vs strict audit completeness.
lane-style (v1_adapter.py): client record matched if ANY exported event has client_corr == rid, else nonce == its
  nonce (first key wins; one event can satisfy two client records).
strict: client record has an event whose CONTENT (nonce in input text) is this request AND whose client_corr is
  this request's rid or empty (i.e. the event is about this request and not mis-attributed).
Also: dispatched-but-unaudited = provider recorded the call (200) and no strict event exists.
usage: audit_strict.py RUN_DIR"""
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
cl = [orjson.loads(l) for l in open_any(f) if l.strip()]
pf = pvd / "records.jsonl.zst"
if not pf.exists(): pf = pvd / "records.jsonl"
prov = {}
for l in open_any(pf):
    if l.strip():
        r = orjson.loads(l); prov[r["rid"]] = r
ev = [orjson.loads(l) for l in open_any(R / "sut" / "v1-events.jsonl.zst") if l.strip()]
by_corr, by_nonce = defaultdict(list), defaultdict(list)
for i, e in enumerate(ev):
    if e.get("client_corr"): by_corr[e["client_corr"]].append(i)
    m = NONCE_RE.search(e.get("inp") or "")
    if m: by_nonce[m.group(0)].append(i)
lane = strict = disp_unaud = 0
ph2 = Counter()
for c in cl:
    lane_hit = bool(by_corr.get(c["rid"]) or by_nonce.get(c.get("nonce")))
    s_hit = any((ev[i].get("client_corr") in (None, "", c["rid"])) for i in by_nonce.get(c.get("nonce"), []))
    lane += lane_hit; strict += s_hit
    p = prov.get(c["rid"])
    if p is not None and not s_hit:
        disp_unaud += 1
    if c.get("ph") == 2:
        ph2["n"] += 1; ph2["lane"] += lane_hit; ph2["strict"] += s_hit
        if p is not None and not s_hit: ph2["dispatched_unaudited"] += 1
        if p is not None: ph2["dispatched"] += 1
n = len(cl)
print(json.dumps({"run": R.name, "client_all_phases": n, "events": len(ev),
                  "lane_style_completeness": round(lane / n, 4), "strict_completeness": round(strict / n, 4),
                  "dispatched_to_provider_but_no_strict_event_all_phases": disp_unaud,
                  "measure_phase": dict(ph2)}))
