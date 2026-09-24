#!/usr/bin/env python3
"""Scaling summary (A) recomputed from every step's pbf_summary.json (itself recomputed from raw).
Capacity rule (labelled "load-knee", = controller rule with T_addon_total in place of T_fw_addon, because
the SSE pattern-aware holdback fails the strict rule at every rate): highest rate whose 3 repeats all pass."""
import json
from collections import defaultdict
from pathlib import Path

RAW = Path("/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-fleet/runs")
SERIES = {"1 x g2-standard-24": "f1u-", "2 x g2-standard-24": "f2u-", "4 x g2-standard-24": "f4u-",
          "fleet 3 x g2-standard-8": "fleet-", "1 x g2-standard-8 (direct)": "f8-", "1 x g2-standard-4 (direct)": "f4-0"}
out = {}
for label, pref in SERIES.items():
    steps = defaultdict(list)
    for d in sorted(RAW.glob(pref + "*")):
        f = d / "pbf_summary.json"
        if not f.exists() or "poisson" in d.name or d.name.endswith("-w1"):
            continue
        s = json.loads(f.read_text())
        rate = (s.get("step") or {}).get("rate")
        steps[rate].append({"run": d.name, "knee_pass": s.get("load_knee_pass"), "strict_pass": s.get("pass"),
                            "infra_pct": round(100 * (s.get("infra_error_rate") or 0), 4),
                            "p99_nohold": s["T_fw_addon_nohold"].get("p99"), "p99_fw": s["T_fw_addon"].get("p99"),
                            "q_rps": s.get("qualified_rps")})
    cap = None
    first_fail = None
    for rate in sorted(steps):
        runs = steps[rate]
        if len(runs) >= 3 and all(r["knee_pass"] for r in runs):
            cap = rate
    for rate in sorted(steps):
        if cap is not None and rate > cap and any(not r["knee_pass"] for r in steps[rate]):
            first_fail = rate
            break
    out[label] = {"capacity_rps": cap, "first_failing_rps": first_fail,
                  "strict_capacity_rps": None if not any(r["strict_pass"] for v in steps.values() for r in v) else "see steps",
                  "steps": {str(k): v for k, v in sorted(steps.items())}}
c1 = out["1 x g2-standard-24"]["capacity_rps"]
for n, lab in ((2, "2 x g2-standard-24"), (4, "4 x g2-standard-24")):
    c = out[lab]["capacity_rps"]
    out[lab]["scaling_efficiency"] = round(c / (c1 * n), 3) if c and c1 else None
Path("/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/"
     "scratchpad/evidence/proto-bench-fleet/scaling_summary.json").write_text(json.dumps(out, indent=1) + "\n")
for lab, v in out.items():
    print(f"{lab:30s} capacity {v['capacity_rps']} first-fail {v['first_failing_rps']} eff {v.get('scaling_efficiency')}")
    for rate, runs in v["steps"].items():
        print(f"    {rate:>5}: " + "; ".join(f"{r['run']} {'P' if r['knee_pass'] else 'F'} infra {r['infra_pct']}% p99nh {r['p99_nohold']} p99fw {r['p99_fw']}" for r in runs))
