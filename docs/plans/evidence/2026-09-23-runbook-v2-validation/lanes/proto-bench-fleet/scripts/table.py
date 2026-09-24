#!/usr/bin/env python3
"""Tabulate every step's pbf_summary.json (recomputed from raw by pbf_analyze.py): one row per step.
  table.py [RUN_PREFIX ...]   (default: all runs under RAW/runs)"""
import json
import sys
from pathlib import Path

RAW = Path("/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-fleet/runs")
pref = sys.argv[1:]
rows = []
for d in sorted(RAW.iterdir()):
    f = d / "pbf_summary.json"
    if not f.exists() or (pref and not any(d.name.startswith(p) for p in pref)):
        continue
    s = json.loads(f.read_text())
    st = s.get("step") or {}
    us = s.get("units_side") or {}
    red = s.get("redis_side") or {}
    edge = s.get("edge_side") or {}
    gpu = []
    wmax = []
    imb = []
    for u, row in (us.get("per_unit") or {}).items():
        for g in (row.get("gpu") or {}).values():
            gpu.append(g["sm_mean"])
        wmax.append(max((row.get("cpu") or {}).get("per_proc_util", {}).get("worker", [0]) or [0]))
        if row.get("per_worker_admitted"):
            imb.append(row["per_worker_admitted"]["max_over_mean"])
    wire = (s.get("wire") or {})
    ewire = wire.get("edge_ip_bytes_per_req") or {}
    uwire = wire.get("unit_ip_bytes_per_req") or {}
    shed = sum(v for k, v in (us.get("worker_counts") or {}).items() if k.startswith("shed{"))
    rows.append({
        "run": d.name, "units": len((st.get("units") or "").split()), "rate": st.get("rate"),
        "strict": "PASS" if s.get("pass") else "FAIL", "knee": "PASS" if s.get("load_knee_pass") else "FAIL",
        "q_rps": s.get("qualified_rps"), "fp%": round(100 * (s.get("fp_rate") or 0), 2),
        "infra%": round(100 * (s.get("infra_error_rate") or 0), 3), "sheds": shed, "drops": s.get("drops"),
        "p99_fw": s["T_fw_addon"].get("p99"), "p99_nohold": s["T_fw_addon_nohold"].get("p99"),
        "p50_nohold": s["T_fw_addon_nohold"].get("p50"),
        "cpu_ms": (us.get("cpu_ms_per_req") or {}).get("gateway"), "gw_cores": us.get("gateway_cores_total"),
        "wkr_util_max": max(wmax) if wmax else None, "imb": max(imb) if imb else None,
        "gpu_sm_mean": round(sum(gpu) / len(gpu), 1) if gpu else None,
        "redis_ops": red.get("ops_per_s"), "redis_ping_p99_us": (red.get("ping_rtt_us") or {}).get("p99_us"),
        "redis_cpu": red.get("redis_cpu_cores"),
        "edge_cores": ((edge.get("cpu") or {}).get("cores_by_role") or {}).get("nginx"),
        "B_edge2cli": ewire.get("edge_to_clients"), "B_cli2edge": ewire.get("clients_to_edge"),
        "B_u2cli": uwire.get("unit_to_client_side"), "B_u2prov": uwire.get("unit_to_provider"),
        "lg_cpu_max": max((x.get("busy_max") or 0) for x in s.get("loadgen") or [{}]),
        "valid": s.get("valid"),
    })
if not rows:
    sys.exit("no runs")
cols = list(rows[0])
print("| " + " | ".join(cols) + " |")
print("|" + "---|" * len(cols))
for r in rows:
    print("| " + " | ".join("" if r[c] is None else str(r[c]) for c in cols) + " |")
