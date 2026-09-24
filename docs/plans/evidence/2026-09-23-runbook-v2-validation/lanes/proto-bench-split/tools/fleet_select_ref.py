#!/usr/bin/env python3
"""$5,000/month ON-DEMAND fleet selection from MEASURED per-unit capacity and MEASURED bytes/request.

  fleet_select.py INPUTS.json [--budget 5000] [--out result.json]

INPUTS.json (every number must come from a measurement step; the file cites the step names):
  {"units": {"g2-standard-24": {"capacity_rps": 55, "source": "..."}, ...},
   "bytes_per_qualified_request": {"lb_to_client": ..., "client_to_lb": ..., "unit_to_provider": ...,
                                   "source": "..."},
   "offered_per_qualified": 1.02}             # offered requests (incl. FP blocks) per qualified request
Prices: SP/evidence/pricing-verifier/prices.json (Cloud Billing Catalog, asia-south1, on-demand, 730 h).
Cost of a fleet at sustained qualified rate R (req/s):
  fixed      = lean bundle F-lead-list ($329.50: regional ext. ALB fwd rule, Memorystore Redis Standard HA
               1 GiB, control-plane n2-standard-4 + 100 GiB boot, 250 GiB pd-balanced data, monitoring)
  compute    = sum over units of (VM on-demand $/mo + 200 GiB pd-balanced boot $24 + external IP $3.65)
  egress     = internet egress of (lb_to_client + unit_to_provider) bytes x R x offered/qualified x 2,628,000
               req/month, tiered (Premium from Mumbai to India by default; Standard tier also shown)
  lb_proc    = ALB data processing $0.01/GiB of (client_to_lb + lb_to_client) bytes
Fleet qualified RPS = min(sum of measured unit capacities, largest R whose total cost <= budget).
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

PRICES = Path("/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/"
              "1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/pricing-verifier/prices.json")
REQ_PER_MONTH_PER_RPS = 730 * 3600  # 2,628,000


def tiered(gib: float, tiers: list[dict]) -> float:
    cost = 0.0
    bounds = [float(t["from_gib"]) for t in tiers] + [float("inf")]
    for i, t in enumerate(tiers):
        lo, hi = bounds[i], bounds[i + 1]
        if gib > lo:
            cost += (min(gib, hi) - lo) * float(t["usd_per_gib"])
    return cost


def load_prices() -> dict:
    p = json.loads(PRICES.read_text())
    extras = p["per_node_extras_usd_per_month"]
    lean_name = [k for k in p["fixed_bundles"] if k.startswith("F-lead-list")][0]
    lean = p["fixed_bundles"][lean_name]
    items = {i["item"]: i["usd_per_month"] for i in p["fixed_items"]}
    return {
        "shapes": {k: v["usd_per_month_730h"] for k, v in p["shapes"].items()},
        "boot": extras["g2_boot_pd_balanced_200gib"], "ip": extras["external_ip_standard_vm"],
        "fixed_total": lean["usd_per_month"], "fixed_items": {i: items[i] for i in lean["items"]},
        "premium_india": p["egress"]["premium_tier_from_mumbai_by_destination"]["india"],
        "premium_americas": p["egress"]["premium_tier_from_mumbai_by_destination"]["americas"],
        "standard": p["egress"]["standard_tier_from_mumbai"],
        "lb_in": float(p["egress"]["regional_external_alb_processing_usd_per_gib"]["inbound"]),
        "lb_out": float(p["egress"]["regional_external_alb_processing_usd_per_gib"]["outbound"]),
        "fetched": p["meta"]["fetched_at_utc"],
    }


def variable_cost(r: float, b: dict, opq: float, pr: dict, tier: str) -> dict:
    req = r * opq * REQ_PER_MONTH_PER_RPS
    egress_gib = req * (b["lb_to_client"] + b["unit_to_provider"]) / 2**30
    lb_in_gib = req * b["client_to_lb"] / 2**30
    lb_out_gib = req * b["lb_to_client"] / 2**30
    tiers = pr["premium_india"] if tier == "premium_india" else pr["standard"]
    egress = tiered(egress_gib, tiers)
    lbp = lb_in_gib * pr["lb_in"] + lb_out_gib * pr["lb_out"]
    return {"requests_month": req, "egress_gib": egress_gib, "egress_usd": egress, "lb_processing_usd": lbp,
            "lb_in_gib": lb_in_gib, "lb_out_gib": lb_out_gib}


def fleet_cost(counts: dict, r: float, inp: dict, pr: dict, tier: str) -> dict:
    compute = sum(n * (pr["shapes"][s] + pr["boot"] + pr["ip"]) for s, n in counts.items())
    v = variable_cost(r, inp["bytes_per_qualified_request"], inp.get("offered_per_qualified", 1.0), pr, tier)
    total = pr["fixed_total"] + compute + v["egress_usd"] + v["lb_processing_usd"]
    return {"fixed": pr["fixed_total"], "compute": compute, **v, "total": total}


def max_affordable(counts: dict, inp: dict, pr: dict, tier: str, budget: float) -> float:
    lo, hi = 0.0, 1e6
    if fleet_cost(counts, 0.0, inp, pr, tier)["total"] > budget:
        return 0.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if fleet_cost(counts, mid, inp, pr, tier)["total"] <= budget:
            lo = mid
        else:
            hi = mid
    return lo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs")
    ap.add_argument("--budget", type=float, default=5000.0)
    ap.add_argument("--max-units", type=int, default=8)
    ap.add_argument("--out")
    a = ap.parse_args()
    inp = json.loads(Path(a.inputs).read_text())
    pr = load_prices()
    shapes = sorted(inp["units"])
    results = {}
    for tier in ("premium_india", "standard"):
        rows = []
        for combo in itertools.product(range(a.max_units + 1), repeat=len(shapes)):
            if sum(combo) == 0:
                continue
            counts = {s: n for s, n in zip(shapes, combo) if n}
            cap = sum(inp["units"][s]["capacity_rps"] * n for s, n in counts.items())
            aff = max_affordable(counts, inp, pr, tier, a.budget)
            if aff <= 0:
                continue
            r = min(cap, aff)
            c = fleet_cost(counts, r, inp, pr, tier)
            rows.append({"counts": counts, "capacity_rps": round(cap, 2), "affordable_rps": round(aff, 2),
                         "fleet_qualified_rps": round(r, 2), "binding": "capacity" if cap <= aff else "budget",
                         "cost_at_r": {k: round(v, 2) for k, v in c.items()}})
        rows.sort(key=lambda x: (-x["fleet_qualified_rps"], x["cost_at_r"]["total"]))
        results[tier] = {"best": rows[0] if rows else None, "top10": rows[:10]}
    out = {"inputs": inp, "prices": {k: v for k, v in pr.items() if k not in ("premium_americas",)},
           "budget": a.budget, "req_per_month_per_rps": REQ_PER_MONTH_PER_RPS, "results": results}
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
    for tier, res in results.items():
        b = res["best"]
        print(f"[{tier}] best: {b['counts']} -> {b['fleet_qualified_rps']} qualified RPS "
              f"(capacity {b['capacity_rps']}, affordable {b['affordable_rps']}, binding {b['binding']}) "
              f"cost {b['cost_at_r']}")
        for row in res["top10"][1:6]:
            print(f"   alt {row['counts']} -> {row['fleet_qualified_rps']} ({row['binding']}) total ${row['cost_at_r']['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
