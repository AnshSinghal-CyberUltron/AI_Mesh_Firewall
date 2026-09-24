#!/usr/bin/env python3
"""reviewer-observability / v1: /health probe (SUT-local GET /health, 5 Hz, fresh connection, 5 s socket timeout;
setup/sut_sampler.py) vs what clients experienced, per 1-s bin of wall time.
Client completion wall = start_at_unix_ms/1000 + (sched_ns + late_ns + end_ns)/1e9 (loadgen clock; GCP NTP, bins
are 1 s so sub-second skew is irrelevant). Error = anything but 200+[DONE]/valid JSON (timeouts are placed at their
120 s timeout instant, i.e. when the client gave up).
usage: health_vs_wire.py RUN_DIR"""
import json, sys
from collections import defaultdict, Counter
from pathlib import Path
import orjson
def open_any(p):
    s = str(p)
    if s.endswith(".zst"):
        from compression import zstd
        return zstd.open(s, "rb")
    return open(s, "rb")
R = Path(sys.argv[1])
lgd = next(R.glob("rv-v1-lg-*/lg"))
man = json.loads((lgd / "manifest.json").read_text())
t0 = man["config"]["start_at_unix_ms"] / 1000.0
f = lgd / "requests.jsonl.zst"
if not f.exists(): f = lgd / "requests.jsonl"
bins = defaultdict(Counter)
for line in open_any(f):
    if not line.strip(): continue
    c = orjson.loads(line)
    t = t0 + (c.get("sched_ns", 0) + c.get("late_ns", 0) + (c.get("end_ns") or 0)) / 1e9
    b = int(t)
    ok = c.get("status") == 200 and not c.get("err") and c.get("done_seen")
    bins[b]["ok" if ok else "err"] += 1
    if not ok:
        bins[b]["timeout" if c.get("err") == "timeout" else f"http_{c.get('status')}"] += 1
probe = defaultdict(list)
for line in open_any(R / "sut" / "probe.jsonl.zst"):
    if line.strip():
        p = orjson.loads(line)
        probe[int(p["wall"])].append(p)
secs = sorted(set(bins) | set(probe))
rows = []
for s in secs:
    pr = probe.get(s, [])
    rows.append({"t": s - int(t0), "ok": bins[s]["ok"], "err": bins[s]["err"], "timeout": bins[s]["timeout"],
                 "http_503": bins[s]["http_503"], "http_422": bins[s]["http_422"],
                 "probes": len(pr), "probe_non200": sum(1 for p in pr if p["status"] != 200),
                 "probe_max_ms": max((p["ms"] for p in pr), default=None)})
active = [r for r in rows if r["ok"] + r["err"] > 0]
err_secs = [r for r in active if r["err"] > 0]
bad = [r for r in active if r["err"] >= 10 and r["probes"] > 0 and r["probe_non200"] == 0]
worst = [r for r in active if r["err"] > 0 and r["err"] >= r["ok"] and r["probes"] > 0 and r["probe_non200"] == 0]
out = {"run": R.name, "probes_total": sum(len(v) for v in probe.values()),
       "probes_non200_total": sum(1 for v in probe.values() for p in v if p["status"] != 200),
       "probe_statuses": dict(Counter(p["status"] for v in probe.values() for p in v)),
       "probe_ms_max": max((p["ms"] for v in probe.values() for p in v), default=None),
       "seconds_with_client_errors": len(err_secs),
       "seconds_errors_ge10_and_all_probes_200": len(bad),
       "seconds_errors_ge_ok_and_all_probes_200": len(worst),
       "client_errors_in_seconds_where_all_probes_200": sum(r["err"] for r in active if r["err"] and r["probes"] and r["probe_non200"] == 0),
       "client_errors_total": sum(r["err"] for r in active),
       "peak_second": max(active, key=lambda r: r["err"]) if active else None}
print(json.dumps(out, indent=1))
