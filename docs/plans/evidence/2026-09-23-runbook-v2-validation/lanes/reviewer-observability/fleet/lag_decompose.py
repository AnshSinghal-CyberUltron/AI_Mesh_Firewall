#!/usr/bin/env python3
"""Decompose the client-side per-stream 'release lag' (harness T_release_lag_max = max_j a[k]-e[j]) into
  (1) the pre-provider addon every piece inherits  = client first_ns - provider recv_to_first_ns for a piece
      released without holdback is not available per piece, so use A0 = T_addon_first (first content BYTE:
      the leading space the gateway releases immediately)
  (2) the per-piece holdback/processing = lag_j - A0
and compare max_j (lag_j - A0) with the gateway's own per-stream release_lag_max histogram.
usage: lag_decompose.py RUN_DIR LG_GLOB PROV_GLOB"""
import math, sys
from pathlib import Path
import orjson
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recompute as R  # noqa: E402

def pct(xs, p):
    xs = sorted(xs); i = max(0, min(len(xs) - 1, math.ceil(p * len(xs)) - 1)); return xs[i]

def main():
    root = Path(sys.argv[1]); lgg, pg = sys.argv[2], sys.argv[3]
    by_rid, calls, n2r, _ = R.load_provider(sorted(p for p in root.glob(pg) if p.is_dir()))
    raw_max, net_max, argmax_idx, per_piece_raw, per_piece_net, a0s = [], [], [], [], [], []
    for d in sorted(p for p in root.glob(lgg) if p.is_dir()):
        f = R.find(d, "requests.jsonl")
        with R.open_any(f) as fh:
            for line in fh:
                c = orjson.loads(line)
                if c.get("ph") != 2 or not c.get("stream") or not c.get("sampled") or c.get("status") != 200:
                    continue
                p = by_rid.get(c["rid"])
                if not p or not p.get("emit_ns") or not c.get("arr_ns"):
                    continue
                e, P, a, C = p["emit_ns"], p["emit_cum"], c["arr_ns"], c["arr_cum"]
                if not P or not C or C[-1] != P[-1]:
                    continue
                a0 = c["first_ns"] - p["recv_to_first_ns"]   # addon of the first released byte
                a0s.append(a0)
                k, n, lags = 0, len(a), []
                for j in range(len(e)):
                    while k < n and C[k] < P[j]:
                        k += 1
                    lags.append(a[k] - e[j])
                m = max(lags)
                raw_max.append(m); net_max.append(m - a0); argmax_idx.append(lags.index(m) / max(1, len(lags) - 1))
                per_piece_raw.extend(lags); per_piece_net.extend(x - a0 for x in lags)
    f = lambda xs: {q: round(pct(xs, q) / 1e6, 2) for q in (0.5, 0.9, 0.99)} | {"mean": round(sum(xs) / len(xs) / 1e6, 2), "n": len(xs)}
    print("streams", len(raw_max))
    print("A0 = T_addon_first (first byte)       ", f(a0s))
    print("client per-stream max lag (harness)   ", f(raw_max))
    print("client per-stream max lag minus A0    ", f(net_max))
    print("client per-piece lag (harness)        ", f(per_piece_raw))
    print("client per-piece lag minus A0         ", f(per_piece_net))
    first_frac = sum(1 for x in argmax_idx if x == 0) / len(argmax_idx)
    print("share of streams whose max is at piece 0:", round(first_frac, 3))

main()
