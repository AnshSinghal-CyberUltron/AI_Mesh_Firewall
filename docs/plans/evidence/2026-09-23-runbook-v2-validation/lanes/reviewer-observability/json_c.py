#!/usr/bin/env python3
"""reviewer-observability: JSON-only T_fw_addon p99 under rule (c) (qualified JSON + JSON infra errors as +inf;
policy blocks excluded), full population, measurement phase."""
import json, math, sys
from pathlib import Path
import orjson
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recompute as R
INF = float("inf")
def pct(xs, p):
    xs = sorted(xs); return xs[max(0, min(len(xs) - 1, math.ceil(p * len(xs)) - 1))]
for r in sys.argv[1:]:
    root = Path.home() / "rv-evidence-raw/proto-bench-unit/runs" / r
    by_rid, calls, _, _ = R.load_provider(sorted(root.glob("*/prov")))
    q, c = [], []
    for d in sorted(root.glob("*/lg")):
        with R.open_any(R.find(d, "requests.jsonl")) as fh:
            for line in fh:
                x = orjson.loads(line)
                if x.get("ph") != 2 or x.get("stream"): continue
                p = by_rid.get(x["rid"]); st = R.stages_of(x.get("stages")); disp = (x.get("disp") or "").upper()
                if x.get("status") in R.BLOCK_STATUSES and disp == "BLOCK" and p is None and not any(v == "U" for v in st.values()): continue
                ok = (x.get("status") == 200 and x.get("done_seen") and not x.get("err") and p is not None and calls.get(x["rid"]) == 1
                      and x.get("content_sha256") == p.get("content_sha256") and disp in R.QUAL_DISP and all(st.get(s) == "E" for s in R.STAGES_DEFAULT.split(",")))
                if ok:
                    v = x["end_ns"] - p["recv_to_last_ns"]; q.append(v); c.append(v)
                else:
                    c.append(INF)
    f = lambda v: v if v == INF else round(v / 1e6, 3)
    print(json.dumps({"run": r, "json_n": len(c), "json_inf": sum(1 for v in c if v == INF), "PUB_json_p99": f(pct(q, .99)), "C_json_p99": f(pct(c, .99))}))
