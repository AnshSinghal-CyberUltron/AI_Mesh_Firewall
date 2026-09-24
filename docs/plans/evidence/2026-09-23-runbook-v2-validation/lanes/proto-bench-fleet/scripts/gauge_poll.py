#!/usr/bin/env python3
"""Runs ON a unit: every 100 ms stat each worker's metrics dump (rewritten every 1 s by the worker) and,
when it changed, record the worker's own exported_at + plan_version_info / killswitch_engaged gauges and
snapshot ages. Output JSONL. Usage: gauge_poll.py OUT.jsonl DURATION_S [METRICS_DIR]"""
import glob, json, os, sys, time
out, dur = sys.argv[1], float(sys.argv[2])
d = sys.argv[3] if len(sys.argv) > 3 else "/dev/shm/rv-metrics"
seen = {}
end = time.time() + dur
with open(out, "w") as fh:
    while time.time() < end:
        for f in glob.glob(os.path.join(d, "worker-*.json")):
            try:
                st = os.stat(f)
            except OSError:
                continue
            if seen.get(f) == st.st_mtime_ns:
                continue
            try:
                x = json.load(open(f))
            except (OSError, ValueError):
                continue
            seen[f] = st.st_mtime_ns
            g = x.get("gauge") or {}
            cnt = x.get("count") or {}
            fh.write(json.dumps({"t_poll": time.time(), "worker": x.get("worker"), "pid": x.get("pid"),
                                 "exported_at": x.get("exported_at"),
                                 "plan": sorted(k for k in g if k.startswith("plan_version_info")),
                                 "ks": sorted(k for k in g if k.startswith("killswitch_engaged")),
                                 "plan_age_s": g.get("plan_snapshot_age_seconds"),
                                 "ks_age_s": g.get("killswitch_snapshot_age_seconds"),
                                 "plan_push_applied": cnt.get("plan_push_applied"), "plan_reloads": cnt.get("plan_reloads"),
                                 "lease_held": {k: v for k, v in g.items() if k.startswith("lease_held")},
                                 "quota_admitted": {k: v for k, v in cnt.items() if k.startswith(("quota_admitted", "quota_rejected", "lease_granted"))},
                                 "lease_refills": cnt.get("lease_refills")}) + "\n")
        time.sleep(0.1)
