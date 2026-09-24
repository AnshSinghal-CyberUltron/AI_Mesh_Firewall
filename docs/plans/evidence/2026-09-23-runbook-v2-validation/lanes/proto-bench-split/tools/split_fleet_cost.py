#!/usr/bin/env python3
"""Monthly on-demand cost (Mumbai list, 730 h) of a split-topology fleet at a sustained qualified rate.

  split_fleet_cost.py --gateways N --guards M --rps R --wire WIRE.json [--budget 5000]

Uses the fleet lane's price loader and model unchanged (tools/fleet_select_ref.py = proto-bench-fleet
fleet_select.py): fixed bundle F-lead-list $329.50 (regional external ALB forwarding rule, Memorystore
Redis Standard HA 1 GiB, control plane n2-standard-4 + 100 GiB, 250 GiB pd-balanced data, monitoring),
ALB data processing, and internet egress of (client-facing + gateway->provider) bytes per qualified request,
tiered Premium from Mumbai to India (Standard tier also shown). Compute = the SUT VMs as deployed:
  gateway c4-highcpu-16 + 50 GiB hyperdisk-balanced boot (baseline) + external IP
  guard   g2-standard-4 + 200 GiB pd-balanced boot (the rv-proto-unit image size) + external IP
WIRE.json: tools/wire_bytes.py output of the fleet's knee runs (bytes per qualified request).
Also solves the largest sustained R whose all-in cost fits --budget (egress grows with R).
Two fixed bases (controller, register C41): "lean" = F-lead-list $329.50; "conservative" = lean + $470.12 to size the
Redis store for the audit stream's retention (2.89 KB/record, MAXLEN 2M/org).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_select_ref as F  # noqa: E402

P = json.loads(F.PRICES.read_text())
STORE_ADDON = 470.12  # register C41 (controller, 2026-09-23)
X = P["per_node_extras_usd_per_month"]
GW = P["shapes"]["c4-highcpu-16"]["usd_per_month_730h"] + X["c4_boot_hdb_50gib_baseline_3000iops_140mibps"] + X["external_ip_standard_vm"]
GUARD = P["shapes"]["g2-standard-4"]["usd_per_month_730h"] + X["g2_boot_pd_balanced_200gib"] + X["external_ip_standard_vm"]


def cost(ngw: int, ng: int, r: float, b: dict, tier: str) -> dict:
    pr = F.load_prices()
    compute = ngw * GW + ng * GUARD
    fixed = pr["fixed_total"]
    bb = {"lb_to_client": b["lb_to_client"], "client_to_lb": b["client_to_lb"], "unit_to_provider": b["gw_to_provider"]}
    v = F.variable_cost(r, bb, 1.0, pr, tier)
    return {"fixed": round(fixed, 2), "compute": round(compute, 2), "compute_plus_fixed": round(fixed + compute, 2),
            "lb_processing": round(v["lb_processing_usd"], 2), "egress_gib": round(v["egress_gib"], 1),
            "egress": round(v["egress_usd"], 2), "all_in": round(fixed + compute + v["lb_processing_usd"] + v["egress_usd"], 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateways", type=int, required=True)
    ap.add_argument("--guards", type=int, required=True)
    ap.add_argument("--rps", type=float, required=True)
    ap.add_argument("--wire", required=True)
    ap.add_argument("--budget", type=float, default=5000.0)
    a = ap.parse_args()
    b = json.loads(Path(a.wire).read_text())["bytes_per_qualified"]
    out = {"per_node_usd_month": {"gateway": round(GW, 2), "guard": round(GUARD, 2)}, "bytes_per_qualified": b,
           "at_measured_rps": {}, "rps_affordable_all_in_within_budget": {}}
    for tier in ("premium_india", "standard"):
        c = cost(a.gateways, a.guards, a.rps, b, tier)
        c["usd_per_qualified_rps_month_compute_plus_fixed"] = round(c["compute_plus_fixed"] / a.rps, 2)
        c["usd_per_qualified_rps_month_all_in"] = round(c["all_in"] / a.rps, 2)
        c["compute_plus_fixed_conservative"] = round(c["compute_plus_fixed"] + STORE_ADDON, 2)
        c["all_in_conservative"] = round(c["all_in"] + STORE_ADDON, 2)
        c["usd_per_qualified_rps_month_compute_plus_fixed_conservative"] = round(c["compute_plus_fixed_conservative"] / a.rps, 2)
        c["usd_per_qualified_rps_month_all_in_conservative"] = round(c["all_in_conservative"] / a.rps, 2)
        c["fits_budget_before_egress"] = {"lean": c["compute_plus_fixed"] <= a.budget, "conservative": c["compute_plus_fixed_conservative"] <= a.budget}
        out["at_measured_rps"][tier] = c
        lo, hi = 0.0, a.rps  # largest sustained R (<= measured capacity) whose all-in cost fits the budget
        for _ in range(60):
            mid = (lo + hi) / 2
            (lo, hi) = (mid, hi) if cost(a.gateways, a.guards, mid, b, tier)["all_in"] <= a.budget else (lo, mid)
        out["rps_affordable_all_in_within_budget"][tier] = round(lo, 1)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
