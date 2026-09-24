#!/usr/bin/env python3
"""SUT resource summary for one run, recomputed from raw files (sampler.jsonl, probe.jsonl on the SUT;
olg manifest.json + requests.jsonl on the loadgen). CPU is cgroup-v2 cpu.stat usage_usec per container.

Measurement window (wall clock) = olg start_at + ramp + warmup .. + duration. Samples are 1 s; the
window's CPU delta is linearly interpolated at the window edges. CPU ms/request = window CPU delta /
number of olg requests SCHEDULED in the window (phase 2). GCE VMs are NTP-synced; wall time is used
only to place a 300 s window, never for latency.

usage: sut_resources.py --sut-dir RUN/sut --lg-dir RUN/lg [--out file.json]
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/harness")
from analyze import open_any, find_raw, loads, pct_sorted  # noqa: E402

GW = "aimeshperf-gateway-1"


def interp(samples, t, key):
    """Linear interpolation of a cumulative counter at wall time t. key(sample)->value."""
    lo = None
    for s in samples:
        if s["wall"] <= t:
            lo = s
        else:
            if lo is None:
                return key(s)
            f = (t - lo["wall"]) / (s["wall"] - lo["wall"])
            return key(lo) + f * (key(s) - key(lo))
    return key(samples[-1])


def dist(v):
    v = sorted(v)
    if not v:
        return None
    return {"n": len(v), "p50": pct_sorted(v, .5), "p90": pct_sorted(v, .9), "p99": pct_sorted(v, .99),
            "p999": pct_sorted(v, .999), "max": v[-1], "mean": round(sum(v) / len(v), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sut-dir", required=True)
    ap.add_argument("--lg-dir", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    man = json.loads((Path(a.lg_dir) / "manifest.json").read_text())
    cfg = man["config"]
    t0 = cfg["start_at_unix_ms"] / 1000.0 + cfg.get("ramp_s", 0) + cfg.get("warmup_s", 0)
    t1 = t0 + cfg["duration_s"]
    n_meas = 0
    with open_any(find_raw(Path(a.lg_dir), "requests.jsonl")) as fh:
        for line in fh:
            if line.strip() and loads(line).get("ph") == 2:
                n_meas += 1
    rows = []
    meta = None
    with open_any(find_raw(Path(a.sut_dir), "sampler.jsonl")) as fh:
        for line in fh:
            if not line.strip():
                continue
            r = loads(line)
            if "meta" in r:
                meta = r["meta"]
                continue
            rows.append(r)
    ctrs = sorted(rows[0]["ctr"].keys())
    win = [r for r in rows if t0 <= r["wall"] <= t1]
    if len(win) < 3:
        sys.exit(f"sampler does not cover the window {t0}..{t1}")
    dt = t1 - t0
    out = {"window": {"t0": t0, "t1": t1, "seconds": dt, "requests_scheduled_in_window": n_meas,
                      "offered_rps": n_meas / dt, "samples_in_window": len(win)},
           "containers": {}}
    tot_ms = 0.0
    for c in ctrs:
        u0 = interp(rows, t0, lambda s: s["ctr"][c]["usage_usec"])
        u1 = interp(rows, t1, lambda s: s["ctr"][c]["usage_usec"])
        cpu_s = (u1 - u0) / 1e6
        tot_ms += cpu_s * 1000
        thr = interp(rows, t1, lambda s: s["ctr"][c]["nr_throttled"]) - interp(rows, t0, lambda s: s["ctr"][c]["nr_throttled"])
        per_s = []
        for i in range(1, len(win)):
            d = (win[i]["ctr"][c]["usage_usec"] - win[i - 1]["ctr"][c]["usage_usec"]) / 1e6
            per_s.append(d / (win[i]["wall"] - win[i - 1]["wall"]))
        out["containers"][c] = {
            "cpu_seconds": round(cpu_s, 3), "cores_mean": round(cpu_s / dt, 3),
            "cores_1s_p99": round(pct_sorted(sorted(per_s), .99), 3) if per_s else None,
            "cores_1s_max": round(max(per_s), 3) if per_s else None,
            "cpu_ms_per_request": round(cpu_s * 1000 / n_meas, 3) if n_meas else None,
            "nr_throttled_delta": thr,
            "mem_anon_mib_max": round(max(r["ctr"][c]["anon"] for r in win) / 2**20, 1),
            "mem_current_mib_max": round(max(r["ctr"][c]["mem_current"] for r in win) / 2**20, 1),
        }
    out["stack_cpu_ms_per_request"] = round(tot_ms / n_meas, 3) if n_meas else None
    # host CPU utilisation from /proc/stat jiffies (user nice system idle iowait irq softirq steal ...)
    h0 = min(win, key=lambda r: r["wall"])["host"]
    h1 = max(win, key=lambda r: r["wall"])["host"]
    tot = sum(h1) - sum(h0)
    idle = (h1[3] + h1[4]) - (h0[3] + h0[4])
    out["host_cpu_util_pct"] = round(100.0 * (tot - idle) / tot, 2) if tot else None
    out["host_cpus"] = (meta or {}).get("nproc")
    # gateway workers: count + RSS
    ws = [r.get("gw_workers") or [] for r in win]
    out["gateway_processes"] = {"min": min(len(w) for w in ws), "max": max(len(w) for w in ws),
                                "pids_seen": len({p["pid"] for w in ws for p in w})}
    rss = [p["rss_kb"] / 1024 for w in ws for p in w]
    out["gateway_worker_rss_mib"] = {"max": round(max(rss), 1) if rss else None,
                                     "sum_max": round(max(sum(p["rss_kb"] for p in w) / 1024 for w in ws), 1)}
    # per-worker CPU share over the window (imbalance across gunicorn workers)
    first = {p["pid"]: p for p in ws[0]}
    last = {p["pid"]: p for p in ws[-1]}
    tck = (meta or {}).get("clk_tck", 100)
    shares = sorted(((last[p]["utime"] + last[p]["stime"]) - (first[p]["utime"] + first[p]["stime"])) / tck / dt
                    for p in last if p in first)
    out["gateway_worker_cores"] = {"n": len(shares), "min": round(shares[0], 3) if shares else None,
                                   "p50": round(pct_sorted(shares, .5), 3) if shares else None,
                                   "max": round(shares[-1], 3) if shares else None}
    # /health probe (event-loop responsiveness) inside the window
    pf = find_raw(Path(a.sut_dir), "probe.jsonl")
    if pf is not None:
        pr = []
        bad = 0
        with open_any(pf) as fh:
            for line in fh:
                if line.strip():
                    x = loads(line)
                    if t0 <= x["wall"] <= t1:
                        if x["status"] == 200:
                            pr.append(x["ms"])
                        else:
                            bad += 1
        out["health_probe_ms"] = dist(pr)
        out["health_probe_failures"] = bad
    s = json.dumps(out, indent=1)
    if a.out:
        Path(a.out).write_text(s)
    print(s)


if __name__ == "__main__":
    main()
