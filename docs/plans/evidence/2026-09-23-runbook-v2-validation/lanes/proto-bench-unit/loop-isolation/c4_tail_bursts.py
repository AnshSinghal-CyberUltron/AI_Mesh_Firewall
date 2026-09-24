#!/usr/bin/env python3
"""Where do the C4 tail events of the li-* runs come from? (proto-bench-unit, evidence for the final report)

For every qualified SSE stream with per-chunk data (-sample-mod 1 runs only), each client event k gets the C4 delay
d_k = a[k] - e[m(k)] (same mapping as pbu_c4.py). d_k is client clock minus provider clock, so every event carries
the stream's input phase (d_0 = T_input + processing of event 0); the mid-stream extra x_k = d_k - d_0 is normally
< 1 ms, so an event k >= 1 with x_k > --thr ms marks a serving-loop stall (or a next-piece release, x_k ~ ITL: see
c4_event_attribution.py). Stalled events are clustered in time (gap
<= --gap ms) and each cluster is reported with: wall time, streams hit, streams in flight at that moment (first
event <= t <= last event), max x_k, and the interval since the previous cluster. The ratio hit / in-flight
tells a per-process stall (a few of the 18 workers) from a host-wide one (all workers).

  c4_tail_bursts.py RUN_DIR [RUN_DIR ...] [--thr 25] [--gap 150]
"""

from __future__ import annotations

import argparse
import bisect
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools" / "pinned"))
import analyze_c9f89fe8 as A  # noqa: E402


def run_one(run: pathlib.Path, thr_ns: float, gap_ns: float) -> None:
    prov = A.Provider()
    prov.load([str(p) for p in sorted(run.glob("*/prov"))])
    stalls = []   # (abs_ns, rid, d_ns)
    spans = []    # (first_abs_ns, last_abs_ns)
    first_ev = 0
    n = 0
    for lgdir in sorted(run.glob("*/lg")):
        man = json.loads((lgdir / "manifest.json").read_text())
        if man["config"].get("sample_mod") != 1:
            raise SystemExit(f"{lgdir}: sample_mod != 1")
        t0 = man["config"]["start_at_unix_ms"] * 1_000_000
        for c in A.read_jsonl(A.find_raw(lgdir, "requests.jsonl")):
            if c.get("ph") != 2 or c.get("status") != 200 or not c.get("stream"):
                continue
            p = prov.by_rid.get(c["rid"])
            if not p or not p.get("emit_ns") or not c.get("arr_ns"):
                continue
            e, pc, a, cc = p["emit_ns"], p["emit_cum"], c["arr_ns"], c["arr_cum"]
            if cc[-1] != pc[-1]:
                continue
            n += 1
            base = t0 + c["sched_ns"]
            spans.append((base + a[0], base + a[-1]))
            j = 0
            d0 = None
            for k in range(len(a)):
                while j < len(e) - 1 and pc[j] < cc[k]:
                    j += 1
                d = a[k] - e[j]
                if d0 is None:
                    d0 = d
                    if d > thr_ns:
                        first_ev += 1
                elif d - d0 > thr_ns:
                    stalls.append((base + a[k], c["rid"], d - d0))
    stalls.sort()
    starts = sorted(s for s, _ in spans)
    ends = sorted(e for _, e in spans)
    clusters = []
    for t, rid, d in stalls:
        if clusters and t - clusters[-1]["t_last"] <= gap_ns:
            cl = clusters[-1]
            cl["t_last"] = t
            cl["rids"].add(rid)
            cl["dmax"] = max(cl["dmax"], d)
        else:
            clusters.append({"t0": t, "t_last": t, "rids": {rid}, "dmax": d})
    print(f"== {run.name}: {n} qualified SSE streams; streams with a mid-stream x_k > {thr_ns / 1e6:.0f} ms: "
          f"{len({r for _, r, _ in stalls})}; streams with d_0 > thr (input phase): {first_ev}; clusters: {len(clusters)}")
    prev = None
    for cl in clusters:
        t = cl["t0"]
        inflight = bisect.bisect_right(starts, t) - bisect.bisect_left(ends, t)
        hit = len(cl["rids"])
        ts = datetime.datetime.fromtimestamp(t / 1e9, datetime.UTC).strftime("%H:%M:%S.%f")[:-3]
        since = "" if prev is None else f"  +{(t - prev) / 1e9:6.1f} s"
        dur = (cl["t_last"] - cl["t0"]) / 1e6
        print(f"  {ts}  streams hit {hit:3d} / in flight {inflight:3d} ({100 * hit / max(inflight, 1):5.1f}%)  "
              f"max x_k {cl['dmax'] / 1e6:6.1f} ms  span {dur:6.1f} ms{since}")
        prev = t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--thr", type=float, default=25.0, help="stall threshold on mid-stream x_k = d_k - d_0, ms")
    ap.add_argument("--gap", type=float, default=150.0, help="max gap between stalled events in one cluster, ms")
    a = ap.parse_args()
    for r in a.runs:
        run_one(pathlib.Path(r), a.thr * 1e6, a.gap * 1e6)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
