#!/usr/bin/env python3
"""scaling.py: the scaling curve (C4 rule, 3 runs at each knee) + efficiency vs (a), from EVID/runs/*/verdict.json.
  scaling.py 'a:s1-191,s1-191-r2,s1-191-r3:1:1' 'b:s2b-298,...:1:2' ...   (name:runs:gateways:guards)"""
import json
import sys
from pathlib import Path

EVID = Path(__file__).resolve().parents[1]
rows = []
for spec in sys.argv[1:]:
    name, runs, ngw, ng = spec.split(":")
    vs = [json.loads((EVID / "runs" / r / "verdict.json").read_text()) for r in runs.split(",")]
    q = [v["qualified_rps"] for v in vs]
    rows.append({"config": name, "gateways": int(ngw), "guards": int(ng), "runs": runs.split(","),
                 "verdicts": [v["verdict"] for v in vs], "c4_p99": [v["c4_p99"] for v in vs],
                 "qualified_rps_mean": round(sum(q) / len(q), 2), "qualified_rps": q})
base = rows[0]["qualified_rps_mean"]
for r in rows:
    r["x_vs_a"] = round(r["qualified_rps_mean"] / base, 3)
    r["efficiency_per_guard_vs_a"] = round(r["qualified_rps_mean"] / (base * r["guards"]), 3)
    r["qualified_rps_per_guard"] = round(r["qualified_rps_mean"] / r["guards"], 1)
    r["qualified_rps_per_gateway"] = round(r["qualified_rps_mean"] / r["gateways"], 1)
print(json.dumps(rows, indent=1))
