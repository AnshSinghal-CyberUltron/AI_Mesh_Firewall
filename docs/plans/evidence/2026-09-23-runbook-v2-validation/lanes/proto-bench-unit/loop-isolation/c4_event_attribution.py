#!/usr/bin/env python3
"""Are mid-stream C4 extras near one ITL (x_k ~ 20 ms) processing stalls or holdback waits? (proto-bench-unit)

C4 maps client event k to m(k) = first provider piece whose cumulative bytes cover the event (pbu_c4.py, same as
reviewer-observability/c4_client.py) and charges d_k = a[k] - e[m(k)] (client clock minus provider clock, so every
d_k carries the stream's input phase: d_0 = T_input + processing of event 0). The mid-stream extra is
x_k = d_k - d_0. If an event ends exactly at a piece boundary but was released only when the NEXT piece arrived (a
holdback wait), x_k ~ ITL although the gateway spent ~0.3 ms. Test per event k >= 1 with x_k > --thr:
nx = a[k] - e[m(k)+1] - d_0; -1 <= nx < --near ms means the event left the gateway right after piece m(k)+1 arrived
("next-piece release"); otherwise it is a stall. Also reports C4 p99 over all qualified requests with
next-piece-release events re-charged as a[k] - e[m(k)+1] (sensitivity only; the controller's C4 definition is
unchanged).

  c4_event_attribution.py RUN_DIR [RUN_DIR ...] [--thr 10] [--near 3]
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import pbu_analyze as P  # noqa: E402

A = P.A


def p99(xs: list[float]) -> float:
    xs = sorted(xs)
    return round(xs[max(0, math.ceil(0.99 * len(xs)) - 1)] / 1e6, 3) if xs else float("nan")


def run_one(run: pathlib.Path, thr: float, near: float) -> None:
    prov = A.Provider()
    prov.load([str(p) for p in sorted(run.glob("*/prov"))])
    files = A.expand([str(p) for p in sorted(run.glob("*/lg"))], "requests.jsonl")
    nonce = Counter()
    for f in files:
        with A.open_any(f) as fh:
            for line in fh:
                if line.strip():
                    nonce[A.loads(line).get("nonce")] += 1
    from types import SimpleNamespace
    args = SimpleNamespace(policy="none", mode="sut")
    stages = "canon,det,sem,resolve,dispatch,out,audit".split(",")
    ev = Counter()
    d_hist = Counter()
    c4, c4_re = [], []
    streams_nextpiece_max, streams_stall_max = 0, 0
    for f in files:
        with A.open_any(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                c = A.loads(line)
                if c.get("ph", 2) != 2:
                    continue
                p = prov.by_rid.get(c["rid"])
                k_, m, _r, _s = P.categorize(c, p, prov.calls.get(c["rid"], 0), args, {}, stages,
                                             nonce[c.get("nonce")] > 1, "sut", "none")
                if k_ != "qualified":
                    continue
                total = m["addon_total"]
                if not c.get("stream"):
                    c4.append(total)
                    c4_re.append(total)
                    continue
                e, pc, a, cc = p["emit_ns"], p["emit_cum"], c["arr_ns"], c["arr_cum"]
                j = 0
                dmax, dmax_re, kind_of_max = 0, 0, "none"
                d0 = None
                for k in range(len(a)):
                    while j < len(e) - 1 and pc[j] < cc[k]:
                        j += 1
                    d = a[k] - e[j]
                    if d0 is None:
                        d0 = d
                    d_re = d
                    kind = "ok"
                    x = d - d0
                    if k >= 1 and x > thr * 1e6:
                        nx = a[k] - e[j + 1] if j + 1 < len(e) else None
                        if nx is not None and -1e6 <= nx - d0 < near * 1e6:
                            kind = "next_piece"
                            d_re = nx
                        else:
                            kind = "stall"
                        ev[kind] += 1
                        d_hist[(kind, int(x // 5e6) * 5)] += 1
                    if d > dmax:
                        dmax, kind_of_max = d, kind
                    dmax_re = max(dmax_re, d_re)
                if kind_of_max == "next_piece":
                    streams_nextpiece_max += 1
                elif kind_of_max == "stall":
                    streams_stall_max += 1
                c4.append(max(total, dmax))
                c4_re.append(max(total, dmax_re))
    print(f"== {run.name}: mid-stream events with x_k = d_k - d_0 > {thr:.0f} ms: next-piece release {ev['next_piece']}, "
          f"stall {ev['stall']}; streams whose C4 max is a next-piece event {streams_nextpiece_max}, a stall "
          f"{streams_stall_max}")
    print("   x_k bins (ms, lower edge): " + ", ".join(f"{k}{b}:{n}" for (k, b), n in sorted(d_hist.items())))
    print(f"   C4 p99 all qualified: as defined {p99(c4)} ms; next-piece events re-charged as nx {p99(c4_re)} ms "
          f"(n={len(c4)})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--thr", type=float, default=10.0)
    ap.add_argument("--near", type=float, default=3.0)
    a = ap.parse_args()
    for r in a.runs:
        run_one(pathlib.Path(r), a.thr, a.near)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
