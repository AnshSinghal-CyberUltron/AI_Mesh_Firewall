"""Experiment spend from Cloud Audit Logs (authoritative create/delete times), priced at each VM's own region
on-demand list price (Cloud Billing Catalog API). VM + boot disk + ephemeral external IP; failed inserts cost 0.

Inputs (same dir): audit_rv_insert_first.json (insert requests), audit_rv_ops.json (operation.last for insert/delete),
tokyo_skus.json; Mumbai prices from ../evidence/pricing-verifier/prices.json.
Usage: python3 spend_audit.py [--now ISO8601]   -> spend_audit.json + table on stdout
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PR = json.load(open(os.path.join(HERE, "..", "evidence", "pricing-verifier", "prices.json")))
U = {k: float(v) for k, v in PR["unit_prices_usd"].items()}
TK = {r["desc"]: r["usd"] for r in json.load(open(os.path.join(HERE, "tokyo_skus.json")))}
SHAPES = {k: v for k, v in PR["shapes"].items()}
IP_H = U["ip_external_standard_vm_h"]  # $0.005/h standard-VM external IP (global SKU)

RATES = {
    "asia-south1": {"g2_core": U["g2_core_h"], "g2_ram": U["g2_ram_gib_h"], "l4": U["l4_gpu_h"],
                    "c4_core": U["c4_core_h"], "c4_ram": U["c4_ram_gib_h"], "pd_bal": U["pd_balanced_gib_mo"],
                    "hdb": U["hdb_capacity_gib_mo"]},
    "asia-northeast1": {"g2_core": TK["G2 Instance Core running in Tokyo"], "g2_ram": TK["G2 Instance Ram running in Tokyo"],
                        "l4": TK["Nvidia L4 GPU running in Tokyo"], "c4_core": TK["C4 Instance Core running in Tokyo"],
                        "c4_ram": TK["C4 Instance Ram running in Tokyo"], "pd_bal": TK["Balanced PD Capacity in Japan"],
                        "hdb": TK["Hyperdisk Balanced Capacity in Tokyo"]},
}
EXTRA_SHAPES = {"c4-standard-4": (4, 15, 0), "c4-standard-8": (8, 30, 0), "c4-standard-16": (16, 60, 0),
                "c4-highcpu-8": (8, 16, 0), "c4-highcpu-16": (16, 32, 0), "g2-standard-4": (4, 16, 1),
                "g2-standard-8": (8, 32, 1), "g2-standard-24": (24, 96, 2)}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def vm_hourly(region, mt):
    r = RATES[region]
    vcpu, ram, gpu = EXTRA_SHAPES[mt]
    fam = mt.split("-")[0]
    return vcpu * r[f"{fam}_core"] + ram * r[f"{fam}_ram"] + gpu * r["l4"]


def disk_hourly(region, size, dtype):
    r = RATES[region]
    per = r["hdb"] if "hyperdisk" in dtype else r["pd_bal"]
    return size * per / 730.0


def main():
    now = datetime.now(timezone.utc)
    if "--now" in sys.argv:
        now = ts(sys.argv[sys.argv.index("--now") + 1])
    first = {e["operation"]["id"]: e for e in json.load(open(os.path.join(HERE, "audit_rv_insert_first.json")))}
    ops = json.load(open(os.path.join(HERE, "audit_rv_ops.json")))
    inserts, deletes, failed = [], defaultdict(list), 0
    for e in ops:
        p = e["protoPayload"]
        name = p["resourceName"].split("/")[-1]
        zone = p["resourceName"].split("/")[3]
        t = ts(e["timestamp"])
        if p["methodName"].endswith("insert"):
            if p.get("status", {}).get("code"):
                failed += 1
                continue
            f = first.get(e["operation"]["id"])
            req = f["protoPayload"]["request"] if f else {}
            mt = req.get("machineType", "").split("/")[-1]
            disks = [(int(d["initializeParams"].get("diskSizeGb", 0)), d["initializeParams"].get("diskType", "").split("/")[-1])
                     for d in req.get("disks", []) if d.get("initializeParams")]
            inserts.append({"name": name, "zone": zone, "t": t, "mt": mt, "disks": disks})
        else:
            deletes[(name, zone)].append(t)
    rows, by_lane, total = [], defaultdict(float), 0.0
    for ins in sorted(inserts, key=lambda x: x["t"]):
        dl = sorted(x for x in deletes[(ins["name"], ins["zone"])] if x > ins["t"])
        end = dl[0] if dl else now
        if dl:
            deletes[(ins["name"], ins["zone"])].remove(dl[0])
        region = ins["zone"].rsplit("-", 1)[0]
        h = (end - ins["t"]).total_seconds() / 3600.0
        vm = vm_hourly(region, ins["mt"])
        dk = sum(disk_hourly(region, s, t) for s, t in ins["disks"])
        cost = (vm + dk + IP_H) * h
        lane = ins["name"].split("-")[1]
        by_lane[lane] += cost
        total += cost
        rows.append({"name": ins["name"], "zone": ins["zone"], "machine_type": ins["mt"], "create_utc": ins["t"].isoformat(),
                     "delete_utc": end.isoformat() if dl else None, "hours": round(h, 3), "usd": round(cost, 2)})
    res = {"as_of_utc": now.isoformat(), "basis": "on-demand list at each VM's region (Mumbai prices.json; Tokyo Catalog API); VM + boot disk + external IP; failed inserts free",
           "successful_inserts": len(inserts), "failed_inserts_stockout": failed, "still_running": sum(1 for r in rows if r["delete_utc"] is None),
           "total_usd": round(total, 2), "by_lane_usd": {k: round(v, 2) for k, v in sorted(by_lane.items())}, "vms": rows}
    json.dump(res, open(os.path.join(HERE, "spend_audit.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "vms"}, indent=1))


if __name__ == "__main__":
    main()
