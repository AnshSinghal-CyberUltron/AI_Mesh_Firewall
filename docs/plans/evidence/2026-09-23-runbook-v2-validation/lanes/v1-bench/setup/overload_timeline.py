#!/usr/bin/env python3
"""Overload timeline for one run: per BIN seconds of wall time — olg scheduled/ok/errors/inflight/conn opens
(timeseries.jsonl), gateway cores + anon memory + busiest worker (SUT sampler), /health probe p50/max.
usage: overload_timeline.py RUN_DIR [BIN_S]"""
import json, sys
from pathlib import Path
sys.path.insert(0, "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/harness")
from analyze import open_any, find_raw, loads, pct_sorted  # noqa: E402
R = Path(sys.argv[1]); B = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
lgd = next(R.glob("rv-v1-lg-*/lg"))
man = json.loads((lgd / "manifest.json").read_text()); cfg = man["config"]
t_start = cfg["start_at_unix_ms"] / 1000.0
ts = [loads(l) for l in open_any(find_raw(lgd, "timeseries.jsonl")) if l.strip()]
samp = [loads(l) for l in open_any(find_raw(R / "sut", "sampler.jsonl")) if l.strip()]
samp = [s for s in samp if "meta" not in s]
probe = [loads(l) for l in open_any(find_raw(R / "sut", "probe.jsonl")) if l.strip()]
GW = "aimeshperf-gateway-1"
print(f"run {R.name}: rate {cfg['rate']} ramp {cfg['ramp_s']} warm {cfg['warmup_s']} dur {cfg['duration_s']}; t=0 is olg start; "
      f"schedule ends t={cfg['ramp_s']+cfg['warmup_s']+cfg['duration_s']:.0f}s; req timeout {cfg['req_timeout_s']}s")
print("| t (s) | olg sched/s | ok/s | err/s | client in-flight (end of bin) | new conns | GW cores | GW anon MiB | busiest worker cores | /health p50 / max ms (fail) |")
print("|---|---|---|---|---|---|---|---|---|---|")
tsk = sorted(ts[0].keys())
def tsval(x, *names):
    for n in names:
        if n in x:
            return x[n]
    return None
tmax = max(s["wall"] for s in samp) - t_start
t = 0.0
while t < tmax:
    a, b = t, t + B
    # olg timeseries counters are CUMULATIVE since start; inflight is instantaneous
    prev = [x for x in ts if (tsval(x, "t") or 0) < a]
    rows = [x for x in ts if a <= (tsval(x, "t") or 0) < b]
    p0 = prev[-1] if prev else {"scheduled": 0, "ok": 0, "errors": 0, "conn_opens": 0}
    p1 = rows[-1] if rows else p0
    sch = p1["scheduled"] - p0["scheduled"]; ok = p1["ok"] - p0["ok"]
    er = p1["errors"] - p0["errors"]; co = p1["conn_opens"] - p0["conn_opens"]
    inf = tsval(p1, "inflight")
    w = [s for s in samp if a <= s["wall"] - t_start < b]
    if len(w) >= 2:
        d = (w[-1]["ctr"][GW]["usage_usec"] - w[0]["ctr"][GW]["usage_usec"]) / 1e6 / (w[-1]["wall"] - w[0]["wall"])
        anon = max(s["ctr"][GW]["anon"] for s in w) / 2**20
        f0 = {p["pid"]: p for p in w[0]["gw_workers"]}; f1 = {p["pid"]: p for p in w[-1]["gw_workers"]}
        bw = max(((f1[p]["utime"] + f1[p]["stime"]) - (f0[p]["utime"] + f0[p]["stime"])) / 100 / (w[-1]["wall"] - w[0]["wall"]) for p in f1 if p in f0) if f1 else None
    else:
        d = anon = bw = None
    pr = sorted(x["ms"] for x in probe if a <= x["wall"] - t_start < b and x["status"] == 200)
    pf = sum(1 for x in probe if a <= x["wall"] - t_start < b and x["status"] != 200)
    print(f"| {a:.0f}-{b:.0f} | {sch/B:.1f} | {ok/B:.1f} | {er/B:.1f} | {inf} | {co} | {'-' if d is None else f'{d:.2f}'} | "
          f"{'-' if anon is None else f'{anon:.0f}'} | {'-' if bw is None else f'{bw:.2f}'} | "
          f"{'-' if not pr else f'{pct_sorted(pr,.5):.1f} / {pr[-1]:.0f}'} ({pf}) |")
    t += B
