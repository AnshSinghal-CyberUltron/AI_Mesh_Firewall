#!/usr/bin/env python3
"""Per-worker event-loop lag inside the measurement window (proto-bench-unit): which workers stalled, how often.

Uses the unit sampler's metric snapshots at the window edges (unit/snap-meas_start, unit/snap-meas_end; in li-* runs
the workers dump them on SIGUSR1) and rvproto's log-bucketed loop_lag_ns histogram (bucket_value mirrors
rvproto/runtime/metrics.py). Prints, per worker, the windowed probe count, samples >= 10 ms and >= 25 ms, and the
largest windowed bucket, so client-side stall bursts (c4_tail_bursts.py) can be matched to gateway-side loop stalls.

  per_worker_looplag.py RUN_DIR [RUN_DIR ...]
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from pbu_analyze import bucket_value  # noqa: E402


def load(d: pathlib.Path) -> dict:
    return {(w["worker"], w["pid"]): w for w in (json.loads(p.read_text()) for p in sorted(d.glob("worker-*.json")))}


def main() -> int:
    for r in sys.argv[1:]:
        u = next(pathlib.Path(r).glob("*/unit"))
        b, a = load(u / "snap-meas_start"), load(u / "snap-meas_end")
        rows, tot10, tot25, workers25 = [], 0, 0, 0
        for key, w in sorted(a.items()):
            hb = b.get(key, {}).get("hist", {}).get("loop_lag_ns", {"b": {}, "n": 0})
            ha = w["hist"]["loop_lag_ns"]
            delta = {int(k): v - hb["b"].get(k, 0) for k, v in ha["b"].items() if v - hb["b"].get(k, 0) > 0}
            n = ha["n"] - hb["n"]
            ge10 = sum(c for k, c in delta.items() if bucket_value(k) >= 10e6)
            ge25 = sum(c for k, c in delta.items() if bucket_value(k) >= 25e6)
            top = max((bucket_value(k) for k in delta), default=0) / 1e6
            tot10 += ge10
            tot25 += ge25
            workers25 += ge25 > 0
            rows.append(f"worker-{key[0]:>2} pid {key[1]}: n {n:6d}  >=10 ms {ge10:3d}  >=25 ms {ge25:3d}  "
                        f"max {top:6.1f} ms" + ("" if key in b else "  (no window-start snapshot)"))
        print(f"== {r}: loop-lag samples >= 10 ms: {tot10}, >= 25 ms: {tot25} on {workers25} of {len(a)} workers")
        for line in rows:
            print("   " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
