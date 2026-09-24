#!/usr/bin/env python3
"""Recompute open-loop ladder results from RAW per-request npz files (never from per-worker summaries).

Works for microbatch_bench.py (raw_[tag]stepNN.npz, one or many processes) and triton_bench.py
(raw_stepNN.npz). Files of the same step from different processes are MERGED before percentiles.
A step PASSES a p99 target iff: every offered request completed without error, 0 schedule drops
(lateness > 5 ms), and merged p99(t_done - t_sched) <= target.
q_safe(target) = highest offered windows/s such that that step and every lower step pass.

usage: analyze_ladder.py <run_dir> [<run_dir> ...]  -> prints table, writes <run_dir>/ladder_recomputed.json
"""
import glob
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402

TARGETS = (5.0, 8.0, 10.0, 15.0, 20.0)


def load_steps(run_dir):
    groups = defaultdict(list)
    for f in sorted(glob.glob(str(Path(run_dir) / "raw_*step*.npz"))):
        k = int(re.search(r"step(\d+)\.npz$", f).group(1))
        groups[k].append(f)
    return groups


def step_metrics(files):
    lat, late, W_all, n_all, comp_all, err_all, offered = [], [], [], 0, 0, 0, 0.0
    t_first, t_last = [], []
    bw = []
    for f in files:
        z = np.load(f)
        i0 = int(z["i0"])
        ts, tsub, td = z["t_sched"][i0:], z["t_submit"][i0:], z["t_done"][i0:]
        err = z["err"][i0:] if "err" in z.files else np.zeros(len(ts), np.int8)
        W = z["W"][i0:] if z["W"].ndim else np.full(len(ts), int(z["W"]))
        ok = (td > 0) & (err == 0)
        n_all += len(ts)
        comp_all += int(ok.sum())
        err_all += int(err.sum())
        lat.append((td[ok] - ts[ok]) / 1e6)
        late.append((tsub - ts) / 1e6)
        W_all.append(W[ok])
        offered += float(z["offered_wps"])
        if ok.any():
            t_first.append(ts.min())
            t_last.append(td[ok].max())
        if "bw" in z.files:
            bw.append(z["bw"][i0:][ok])
    lat = np.concatenate(lat) if lat else np.array([])
    late = np.concatenate(late) if late else np.array([])
    W_all = np.concatenate(W_all) if W_all else np.array([])
    span = (max(t_last) - min(t_first)) / 1e9 if t_first else float("nan")
    m = {"procs": len(files), "offered_wps": round(offered, 1), "n": n_all, "completed": comp_all, "errors": err_all,
         "achieved_wps": round(float(W_all.sum()) / span, 1) if comp_all else 0.0,
         "lat_ms": C.summarize_ms(lat) if comp_all else None,
         "lateness_ms": C.summarize_ms(late) if len(late) else None,
         "drops_gt5ms": int((late > 5.0).sum())}
    if bw:
        m["mean_batch_windows"] = round(float(np.concatenate(bw).mean()), 2)
    return m


def analyze(run_dir):
    groups = load_steps(run_dir)
    steps = []
    for k in sorted(groups):
        s = step_metrics(groups[k])
        s["step"] = k
        steps.append(s)
    steps.sort(key=lambda s: s["offered_wps"])
    q = {}
    for t in TARGETS:
        best, first_fail = 0.0, None
        for s in steps:
            ok = (s["completed"] == s["n"] and s["errors"] == 0 and s["drops_gt5ms"] == 0
                  and s["lat_ms"] is not None and s["lat_ms"]["p99"] <= t)
            if ok and first_fail is None:
                best = s["offered_wps"]
            elif not ok and first_fail is None:
                first_fail = s["offered_wps"]
        q[f"p99<={t:g}ms"] = {"q_safe_wps": best, "first_failing_wps": first_fail}
    res = {"run_dir": str(run_dir), "steps": steps, "q_safe": q}
    Path(run_dir, "ladder_recomputed.json").write_text(json.dumps(res, indent=1))
    return res


def main():
    for d in sys.argv[1:]:
        r = analyze(d)
        print(f"== {d}")
        for s in r["steps"]:
            L = s["lat_ms"] or {}
            print(f"  offered {s['offered_wps']:8.1f} w/s achieved {s['achieved_wps']:8.1f}  p50 {L.get('p50', float('nan')):7.3f} "
                  f"p99 {L.get('p99', float('nan')):8.3f} max {L.get('max', float('nan')):8.3f}  drops {s['drops_gt5ms']} "
                  f"late_p99 {(s['lateness_ms'] or {}).get('p99')}  done {s['completed']}/{s['n']}  batch {s.get('mean_batch_windows')}")
        print("  q_safe:", json.dumps(r["q_safe"]))


if __name__ == "__main__":
    main()
