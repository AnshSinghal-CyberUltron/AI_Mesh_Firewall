#!/usr/bin/env python3
"""split lane cost line: Mumbai on-demand list (evidence/pricing-verifier/prices.json), 730 h/month.

  split_cost.py SPLIT1_QRPS [SPLIT2_QRPS] [UNIT_QRPS]
Configurations (what the SUT VMs actually were; harness loadgen/provider VMs excluded):
  split-1   c4-highcpu-16 gateway (50 GiB hyperdisk-balanced boot, baseline) + ext IP
            + g2-standard-4 guard (200 GiB pd-balanced boot: the rv-proto-unit image size) + ext IP
  split-2   split-1 + a second g2-standard-4 guard (same disk + IP)
  unit-1    g2-standard-24 all-in-one (200 GiB pd-balanced boot) + ext IP  (the unit lane's shape)
Output: $/month and $ per qualified RPS-month (= $/month / qualified RPS at the knee).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SP = Path(__file__).resolve().parents[3]
P = json.loads((SP / "evidence" / "pricing-verifier" / "prices.json").read_text())
SH, X = P["shapes"], P["per_node_extras_usd_per_month"]
IP = X["external_ip_standard_vm"]
GW = SH["c4-highcpu-16"]["usd_per_month_730h"] + X["c4_boot_hdb_50gib_baseline_3000iops_140mibps"] + IP
GUARD = SH["g2-standard-4"]["usd_per_month_730h"] + X["g2_boot_pd_balanced_200gib"] + IP
UNIT = SH["g2-standard-24"]["usd_per_month_730h"] + X["g2_boot_pd_balanced_200gib"] + IP
CONF = {"split-1": GW + GUARD, "split-2": GW + 2 * GUARD, "unit-1 (g2-standard-24)": UNIT}


def main() -> int:
    q = [float(x) for x in sys.argv[1:]]
    names = list(CONF)
    out = {"basis": P["meta"]["basis"], "region_prices": P["meta"]["region"], "hours_per_month": P["meta"]["hours_per_month"],
           "components_usd_month": {"c4-highcpu-16 + 50 GiB HdB + IP": round(GW, 2), "g2-standard-4 + 200 GiB pd-balanced + IP": round(GUARD, 2),
                                    "g2-standard-24 + 200 GiB pd-balanced + IP": round(UNIT, 2)}, "configs": {}}
    for i, name in enumerate(names):
        row = {"usd_month": round(CONF[name], 2)}
        if i < len(q) and q[i] > 0:
            row["qualified_rps"] = q[i]
            row["usd_per_qualified_rps_month"] = round(CONF[name] / q[i], 2)
        out["configs"][name] = row
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
