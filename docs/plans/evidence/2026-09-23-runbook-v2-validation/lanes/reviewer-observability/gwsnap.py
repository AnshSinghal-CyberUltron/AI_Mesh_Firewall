#!/usr/bin/env python3
"""reviewer-observability: gateway-side (SUT) histogram + counter deltas between two metric snapshots.

The rvproto workers/owners dump cumulative HDR-style histograms (6 sub-bucket bits, rvproto/runtime/metrics.py)
and counters every 1 s; the bench samplers copied them at pre / meas_start / meas_end / post.
Delta(histogram) = per-bucket count difference (exact integers), merged over workers by addition, quantiles by
nearest rank on the merged bucket counts (bucket midpoint, capped at the window max is NOT possible for deltas:
the reported quantile is the bucket midpoint; relative error <= 2^-6).

usage: gwsnap.py SNAPDIR_A SNAPDIR_B [--hist name,...]   (SNAPDIR = .../snap-meas_start etc.)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SUB = 6
_HALF = 1 << SUB
_MASK = _HALF - 1


def bucket_value(idx: int) -> float:
    if idx < _HALF:
        return float(idx)
    shift = (idx >> SUB) - 1
    lo = (_HALF | (idx & _MASK)) << shift
    return lo + ((1 << shift) - 1) / 2.0


def load(d: Path, stem: str) -> dict[str, dict]:
    out = {}
    for f in sorted(d.glob(f"{stem}-*.json")):
        try:
            out[f.name] = json.loads(f.read_text())
        except (OSError, ValueError):
            pass
    return out


def delta(a: dict[str, dict], b: dict[str, dict]):
    """Merged delta b - a over processes present in b. Returns (hist, counts, notes)."""
    hist: dict[str, dict] = {}
    counts: dict[str, int] = {}
    notes = []
    for name, eb in b.items():
        ea = a.get(name)
        if ea is not None and ea.get("pid") != eb.get("pid"):
            notes.append(f"{name}: pid changed {ea.get('pid')}->{eb.get('pid')} (process restarted; delta = b)")
            ea = None
        for hn, hb in eb.get("hist", {}).items():
            ha = (ea or {}).get("hist", {}).get(hn, {"n": 0, "sum": 0, "b": {}})
            agg = hist.setdefault(hn, {"n": 0, "sum": 0, "b": {}, "max_b": 0})
            agg["n"] += hb["n"] - ha["n"]
            agg["sum"] += hb["sum"] - ha["sum"]
            agg["max_b"] = max(agg["max_b"], hb.get("max", 0))
            for k, cb in hb["b"].items():
                dv = cb - ha["b"].get(k, 0)
                if dv:
                    agg["b"][k] = agg["b"].get(k, 0) + dv
        for cn, vb in eb.get("count", {}).items():
            va = (ea or {}).get("count", {}).get(cn, 0)
            counts[cn] = counts.get(cn, 0) + vb - va
    return hist, counts, notes


def quantiles(h: dict, qs=(0.5, 0.9, 0.99, 0.999)) -> dict:
    items = sorted((int(k), c) for k, c in h["b"].items() if c)
    n = sum(c for _, c in items)
    out = {"n": n, "mean_ms": round(h["sum"] / h["n"] / 1e6, 4) if h["n"] else None}
    for q in qs:
        rank = q * n
        acc = 0
        val = None
        for i, c in items:
            acc += c
            if acc >= rank:
                val = bucket_value(i)
                break
        out[f"p{q * 100:g}_ms"] = round(val / 1e6, 4) if val is not None else None
    if items:
        out["top_bucket_ms"] = round(bucket_value(items[-1][0]) / 1e6, 3)
    return out


def summarize(sa: Path, sb: Path, stems=("worker", "owner")) -> dict:
    res = {}
    for stem in stems:
        a, b = load(sa, stem), load(sb, stem)
        hist, counts, notes = delta(a, b)
        res[stem] = {"procs_a": len(a), "procs_b": len(b), "notes": notes,
                     "hist": {k: quantiles(v) for k, v in sorted(hist.items())},
                     "count": dict(sorted(counts.items()))}
        # gauges at b (per process)
        res[stem]["gauges_b"] = {name: e.get("gauge", {}) for name, e in b.items()}
    for f in ("t.txt",):
        for lab, d in (("a", sa), ("b", sb)):
            p = d / f
            if p.exists():
                res[f"t_{lab}"] = float(p.read_text().strip())
    return res


if __name__ == "__main__":
    a, b = Path(sys.argv[1]), Path(sys.argv[2])
    r = summarize(a, b)
    if "--gauges" not in sys.argv:
        for stem in ("worker", "owner"):
            r[stem].pop("gauges_b", None)
    print(json.dumps(r, indent=1))
