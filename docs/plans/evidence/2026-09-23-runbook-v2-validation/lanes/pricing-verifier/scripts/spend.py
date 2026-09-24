"""Turn every lane's vm-ledger.jsonl into on-demand list spend (VM + boot disk + external IP).

Usage: python3 spend.py [--refresh-disks]
  --refresh-disks  re-read `gcloud compute disks list` (read-only) and merge into raw/disk_snapshot_cache.json,
                   so disks of VMs deleted later are still priced. Run it while lanes' VMs exist.
Prices come from ../prices.json (built by compute.py from the Cloud Billing Catalog API).
"""
import glob, json, os, subprocess, sys
from datetime import datetime, timezone
from decimal import Decimal as D

OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.dirname(OUT)
PR = json.load(open(os.path.join(OUT, "prices.json")))
U = {k: D(v) for k, v in PR["unit_prices_usd"].items()}
CACHE = os.path.join(OUT, "raw", "disk_snapshot_cache.json")


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def disk_month(d):
    size = D(d["sizeGb"]); t = d["type"]
    if t == "hyperdisk-balanced":
        iops = D(d.get("provisionedIops") or 3000); tp = D(d.get("provisionedThroughput") or 140)
        return size * U["hdb_capacity_gib_mo"] + max(iops - 3000, D(0)) * U["hdb_iops_mo"] + max(tp - 140, D(0)) * U["hdb_throughput_mibps_mo"]
    return size * {"pd-balanced": U["pd_balanced_gib_mo"], "pd-ssd": U["pd_ssd_gib_mo"], "pd-standard": U["pd_standard_gib_mo"]}[t]


cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
if "--refresh-disks" in sys.argv:
    live = json.loads(subprocess.check_output(
        ["gcloud", "compute", "disks", "list", "--project", "ai-mesh-firewall",
         "--format=json(name,zone.basename(),type.basename(),sizeGb,provisionedIops,provisionedThroughput,users.basename(),creationTimestamp)"]))
    for d in live:
        cache[d["name"]] = d
    json.dump(cache, open(CACHE, "w"), indent=1)

now = datetime.now(timezone.utc)
rows, tot, burn = [], D(0), D(0)
for f in sorted(glob.glob(os.path.join(EVID, "*", "vm-ledger*.jsonl"))):
    lane = os.path.basename(os.path.dirname(f))
    for line in open(f):
        if not line.strip():
            continue
        v = json.loads(line)
        shape = PR["shapes"].get(v["machine_type"])
        start = ts(v["create_utc"]); end = ts(v["delete_utc"]) if v.get("delete_utc") else now
        hours = D(str(max((end - start).total_seconds(), 0) / 3600))
        vm_h = D(shape["usd_per_hour"]["total"]) if shape else None
        disks = [d for d in cache.values() if v["name"] in (d.get("users") or []) or d["name"] == v["name"]]
        disk_h = sum((disk_month(d) / 730 for d in disks), D(0))
        ip_h = U["ip_external_standard_vm_h"]
        per_h = (vm_h or D(0)) + disk_h + ip_h
        cost = per_h * hours
        tot += cost
        if not v.get("delete_utc"):
            burn += per_h
        rows.append({"lane": lane, "name": v["name"], "machine_type": v["machine_type"], "running": not v.get("delete_utc"),
                     "hours": round(float(hours), 3), "vm_usd_h": float(vm_h) if vm_h is not None else "UNPRICED_SHAPE",
                     "disk_usd_h": round(float(disk_h), 6), "disks_priced": [d["name"] for d in disks],
                     "ip_usd_h": float(ip_h), "usd_to_date": round(float(cost), 2)})

res = {"as_of_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "basis": "on-demand list (prices.json); IP charge ignores the 720 free IP-h/account",
       "total_usd_to_date": round(float(tot), 2), "current_burn_usd_per_hour": round(float(burn), 4), "vms": rows}
json.dump(res, open(os.path.join(OUT, "spend.json"), "w"), indent=1)
for r in rows:
    print(f"{r['lane']:<14} {r['name']:<18} {r['machine_type']:<15} run={str(r['running']):<5} h={r['hours']:>7.3f} "
          f"vm/h={r['vm_usd_h']} disk/h={r['disk_usd_h']} ${r['usd_to_date']}")
print(f"TOTAL to date ${res['total_usd_to_date']}  current burn ${res['current_burn_usd_per_hour']}/h  as of {res['as_of_utc']}")
