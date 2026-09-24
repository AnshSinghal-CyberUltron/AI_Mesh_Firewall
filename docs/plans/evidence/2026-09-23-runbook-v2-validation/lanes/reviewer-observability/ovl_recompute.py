#!/usr/bin/env python3
"""reviewer-observability: independent recompute of the GW19 overload step ovl-234 (base 39/s on lg-1 for
ramp 30 + warm 60 + 540 s; burst 195/s on lg-2 for 180 s starting 180 s into lg-1's measurement).
Windows on lg-1's schedule: pre [90,270) burst [270,450) post [450,630); lg-2 records all belong to burst.
Per window: offered, qualified, 503 sheds, other infra, policy blocks; T_addon_total over qualified and over
(qualified + infra as +inf) (rule c); shed response time; probe statuses; gateway whole-run counters."""
import json, math, sys
from collections import Counter
from pathlib import Path
import orjson
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recompute as R
INF = float("inf")
def pct(xs, p):
    xs = sorted(xs); return None if not xs else xs[max(0, min(len(xs) - 1, math.ceil(p * len(xs)) - 1))]
f = lambda v: v if v in (None, INF) else round(v / 1e6, 3)
run = Path.home() / "rv-evidence-raw/proto-bench-unit/runs/ovl-234"
by_rid, calls, n2r, nprov = R.load_provider(sorted(run.glob("*/prov")))
W = {"pre": (90, 270), "burst": (270, 450), "post": (450, 630)}
stat = {k: Counter() for k in W}; tot = {k: [] for k in W}; totc = {k: [] for k in W}; shedlat = []
allc = Counter()
for lg in ("rv-pbu-lg-1", "rv-pbu-lg-2"):
    with R.open_any(R.find(run / lg / "lg", "requests.jsonl")) as fh:
        for line in fh:
            c = orjson.loads(line)
            allc[f"{lg}_status_{c.get('status')}"] += 1
            if lg == "rv-pbu-lg-1":
                s = c["sched_ns"] / 1e9
                win = next((k for k, (a, b) in W.items() if a <= s < b), None)
            else:
                win = "burst" if c.get("ph") == 2 else None
            if win is None: continue
            st = stat[win]; st["offered"] += 1
            p = by_rid.get(c["rid"]); status = c.get("status", 0); disp = (c.get("disp") or "").upper()
            stg = R.stages_of(c.get("stages"))
            if status in R.BLOCK_STATUSES and disp == "BLOCK" and p is None and not any(v == "U" for v in stg.values()):
                st["policy_block"] += 1; continue
            ok = (status == 200 and c.get("done_seen") and not c.get("err") and p is not None and calls.get(c["rid"]) == 1
                  and c.get("content_sha256") == p.get("content_sha256") and disp in R.QUAL_DISP
                  and all(stg.get(x) == "E" for x in R.STAGES_DEFAULT.split(",")))
            if ok:
                st["qualified"] += 1; v = c["end_ns"] - p["recv_to_last_ns"]; tot[win].append(v); totc[win].append(v)
            else:
                st["shed_503" if status == 503 else "infra_other"] += 1; totc[win].append(INF)
                if status == 503: shedlat.append(c.get("end_ns") or 0)
out = {}
for k, (a, b) in W.items():
    d = b - a; st = stat[k]
    out[k] = {**st, "offered_per_s": round(st["offered"] / d, 2), "qualified_per_s": round(st["qualified"] / d, 2),
              "shed_share": round(st["shed_503"] / st["offered"], 4) if st["offered"] else None,
              "T_addon_total_qualified_p99": f(pct(tot[k], .99)), "T_addon_total_rule_c_p99": f(pct(totc[k], .99))}
pr = Counter(); ra = Counter()
pf = R.find(run / "rv-pbu-lg-2", "probe.jsonl")
if pf:
    with R.open_any(pf) as fh:
        for line in fh:
            x = orjson.loads(line); pr[str(x.get("status"))] += 1
            ra[f"{x.get('status')}|{x.get('retry_after')}|{x.get('retry_after_ms')}"] += 1
gw = json.loads((Path(__file__).resolve().parent / "out/unit/ovl-234.gw.whole.json").read_text()) if (Path(__file__).resolve().parent / "out/unit/ovl-234.gw.whole.json").exists() else None
print(json.dumps({"windows": out, "shed_response_ms": {q: f(pct(shedlat, q)) for q in (.5, .99)}, "probe": dict(pr),
                  "probe_detail": dict(ra.most_common(8)), "client_all": dict(allc), "provider_records": nprov,
                  "gw_whole": gw["worker"]["count"] if gw else None}, indent=1))
