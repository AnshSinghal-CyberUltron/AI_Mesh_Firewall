#!/usr/bin/env python3
"""On-demand list spend of this lane's VMs from vm-ledger.jsonl and the pricing lane's prices.json (asia-south1).
Compute = cores x core_h + GiB x ram_h (+ L4 x l4_gpu_h); boot disk = GiB x $/GiB-month / 730; external IP 0.005 $/h.
Hyperdisk Balanced at 60 GiB stays inside the free 3,000 IOPS / 140 MiB/s baseline (no IOPS/throughput charge)."""
import datetime as dt
import json
import sys
from pathlib import Path

ev = Path(sys.argv[1])
P = json.loads(Path(sys.argv[2]).read_text())["unit_prices_usd"]
p = {k: float(v) for k, v in P.items() if isinstance(v, str) and v.replace(".", "", 1).replace("E-", "").isdigit()}
shape = {
    "g2-standard-8": dict(cores=8, ram=32, gpu=1, fam="g2", disk=200, disk_price=p["pd_balanced_gib_mo"]),
    "c4-standard-8": dict(cores=8, ram=30, gpu=0, fam="c4", disk=60, disk_price=p["hdb_capacity_gib_mo"]),
    "c4-highcpu-16": dict(cores=16, ram=32, gpu=0, fam="c4", disk=60, disk_price=p["hdb_capacity_gib_mo"]),
}
rows, total = [], 0.0
for line in (ev / "vm-ledger.jsonl").read_text().splitlines():
    r = json.loads(line)
    s = shape[r["machine_type"]]
    t0 = dt.datetime.fromisoformat(r["create_utc"].replace("Z", "+00:00"))
    t1 = dt.datetime.fromisoformat(r["delete_utc"].replace("Z", "+00:00"))
    h = (t1 - t0).total_seconds() / 3600
    rate = (s["cores"] * p[f"{s['fam']}_core_h"] + s["ram"] * p[f"{s['fam']}_ram_gib_h"] + s["gpu"] * p["l4_gpu_h"]
            + s["disk"] * s["disk_price"] / 730 + p["ip_external_standard_vm_h"])
    cost = h * rate
    total += cost
    rows.append({**r, "hours": round(h, 4), "usd_per_h": round(rate, 4), "usd": round(cost, 2)})
out = {"basis": "on-demand list, asia-south1, prices from pricing-verifier/prices.json", "vms": rows,
       "gpu_vm_hours": round(sum(x["hours"] for x in rows if x["machine_type"].startswith("g2")), 3),
       "cpu_vm_hours": round(sum(x["hours"] for x in rows if not x["machine_type"].startswith("g2")), 3),
       "total_usd": round(total, 2)}
(ev / "spend.json").write_text(json.dumps(out, indent=1))
for x in rows:
    print(f"{x['name']:15s} {x['machine_type']:14s} {x['zone']:14s} {x['create_utc']} -> {x['delete_utc']}  {x['hours']:6.3f} h x ${x['usd_per_h']:.4f} = ${x['usd']:6.2f}")
print("GPU VM-h", out["gpu_vm_hours"], "CPU VM-h", out["cpu_vm_hours"], "TOTAL $", out["total_usd"])
