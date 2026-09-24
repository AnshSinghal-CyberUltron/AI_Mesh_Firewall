#!/usr/bin/env python3
"""Spend estimate from vm-ledger.jsonl at asia-south1 on-demand list prices (prices.json shapes/unit prices).
The lane ran in asia-northeast1 (Tokyo), whose list prices differ; this is a Mumbai-price proxy."""
import json, sys
from datetime import datetime, timezone
P = json.load(open('/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/pricing-verifier/prices.json'))
u = {k: float(v) for k, v in P['unit_prices_usd'].items()}
shapes = P['shapes']
def vm_h(mt):
    if mt in shapes:
        return shapes[mt]['usd_per_hour_total']
    fam, kind, n = mt.split('-')
    n = int(n)
    mem = {'highcpu': 2, 'standard': 3.75}[kind] * n if fam == 'c4' else None
    return n * u['c4_core_h'] + mem * u['c4_ram_gib_h']
def disk_h(mt):
    if mt.startswith('g2'):
        return 200 * u['pd_balanced_gib_mo'] / 730
    return 50 * u['hdb_capacity_gib_mo'] / 730
now = datetime.now(timezone.utc)
tot = 0
for line in open(sys.argv[1]):
    r = json.loads(line)
    t0 = datetime.strptime(r['create_utc'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    t1 = datetime.strptime(r['delete_utc'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc) if r.get('delete_utc') else now
    h = (t1 - t0).total_seconds() / 3600
    c = h * (vm_h(r['machine_type']) + disk_h(r['machine_type']) + u['ip_external_standard_vm_h'])
    tot += c
    print(f"{r['name']:18s} {r['zone']:18s} {r['machine_type']:15s} {r['create_utc']} -> {r.get('delete_utc') or 'RUNNING':20s} {h:6.2f} h ${c:7.2f}")
print(f"TOTAL ${tot:.2f} (Mumbai on-demand list proxy, incl. boot disk + external IP)")
