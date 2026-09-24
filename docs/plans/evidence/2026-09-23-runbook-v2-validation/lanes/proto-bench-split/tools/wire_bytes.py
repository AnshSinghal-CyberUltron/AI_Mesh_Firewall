#!/usr/bin/env python3
"""wire_bytes.py RUN_DIR -> NIC bytes per offered request over the whole step (all phases), from the
/proc/net/dev snapshots split_step.sh takes before/after on every VM (all non-lo interfaces):
  lb_to_client     loadgen RX   (edge/gateway -> clients: what the internet egress would carry)
  client_to_lb     loadgen TX
  gw_to_provider   synthprov RX (gateways -> LLM provider: also internet egress in production)
  provider_to_gw   synthprov TX
  gw_to_guard / guard_to_gw   guard VMs RX / TX
plus offered_per_qualified (measurement phase) to convert to per-qualified bytes."""
import json
import sys
from pathlib import Path


def netdev(p: Path) -> dict:
    out = {}
    if not p.exists():
        return out
    for line in p.read_text().splitlines()[2:]:
        name, rest = line.split(":", 1)
        v = rest.split()
        if name.strip() != "lo":
            out[name.strip()] = (int(v[0]), int(v[8]))
    return out


def delta(d: Path) -> tuple[int, int]:
    b, a = netdev(d / "netdev.before"), netdev(d / "netdev.after")
    rx = sum(a[k][0] - b[k][0] for k in a if k in b)
    tx = sum(a[k][1] - b[k][1] for k in a if k in b)
    return rx, tx


run = Path(sys.argv[1])
st = json.loads((run / "step.json").read_text())
lgs, provs = st["lgs"].split(), (st.get("provs") or st.get("prov", "")).split()
guards = st.get("guards", "").split() if st.get("mode") == "sut" else []
n_all = 0
for lg in lgs:
    man = json.loads((run / lg / "lg" / "manifest.json").read_text())
    n_all += (man.get("counts") or {}).get("recorded", 0)
tot = {"lb_to_client": 0, "client_to_lb": 0, "gw_to_provider": 0, "provider_to_gw": 0, "gw_to_guard": 0, "guard_to_gw": 0}
for lg in lgs:
    rx, tx = delta(run / lg)
    tot["lb_to_client"] += rx
    tot["client_to_lb"] += tx
for pv in provs:
    rx, tx = delta(run / pv)
    tot["gw_to_provider"] += rx
    tot["provider_to_gw"] += tx
for g in guards:
    rx, tx = delta(run / g)
    tot["gw_to_guard"] += rx
    tot["guard_to_gw"] += tx
m = json.loads((run / "split_metrics.json").read_text())
opq = m["offered"] / m["qualified"] if m.get("qualified") else None
out = {"run": run.name, "requests_all_phases": n_all, "offered_per_qualified_meas": round(opq, 5) if opq else None,
       "bytes_per_offered": {k: round(v / n_all, 1) for k, v in tot.items()},
       "bytes_per_qualified": {k: round(v / n_all * opq, 1) for k, v in tot.items()} if opq else None}
print(json.dumps(out))
