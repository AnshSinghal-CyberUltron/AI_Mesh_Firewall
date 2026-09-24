"""Per-worker metrics: HDR-style log-bucket histograms (ns) + counters.

Bucketing keeps the top SUB+1 significant bits of a value (relative error
<= 2^-SUB). Buckets are exact integer counts, so worker histograms merge by
addition and percentiles are computed over merged samples, never averaged.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

SUB = 6
_HALF = 1 << SUB
_MASK = _HALF - 1
_NBUCKETS = (64 - SUB + 1) << SUB
QUANTILES = (0.5, 0.9, 0.99, 0.999)


def bucket_of(v: int) -> int:
    if v < _HALF:
        return max(v, 0)
    shift = v.bit_length() - SUB - 1
    return ((shift + 1) << SUB) + ((v >> shift) & _MASK)


def bucket_value(idx: int) -> float:
    """Midpoint of the bucket's value range."""
    if idx < _HALF:
        return float(idx)
    shift = (idx >> SUB) - 1
    lo = (_HALF | (idx & _MASK)) << shift
    return lo + ((1 << shift) - 1) / 2.0


class Histogram:
    __slots__ = ("counts", "n", "total", "vmax", "vmin")

    def __init__(self) -> None:
        self.counts = [0] * _NBUCKETS
        self.n = 0
        self.total = 0
        self.vmax = 0
        self.vmin = -1

    def record(self, v: int) -> None:
        self.counts[bucket_of(v)] += 1
        self.n += 1
        self.total += v
        if v > self.vmax:
            self.vmax = v
        if self.vmin < 0 or v < self.vmin:
            self.vmin = v

    def quantile(self, q: float) -> float:
        return quantile_of(self.counts, self.n, q, self.vmax)

    def export(self) -> dict[str, object]:
        sparse = {str(i): c for i, c in enumerate(self.counts) if c}
        return {"n": self.n, "sum": self.total, "max": self.vmax, "min": self.vmin, "b": sparse}


def quantile_of(counts: list[int], n: int, q: float, vmax: int) -> float:
    if n == 0:
        return 0.0
    rank = q * n
    acc = 0
    for i, c in enumerate(counts):
        if not c:
            continue
        acc += c
        if acc >= rank:
            return min(bucket_value(i), float(vmax))
    return float(vmax)


class Registry:
    """One per worker process; created at startup and passed to components."""

    def __init__(self, worker: int) -> None:
        self.worker = worker
        self.pid = os.getpid()
        self.started = time.time()
        self.hist: dict[str, Histogram] = {}
        self.count: dict[str, int] = {}
        self.gauge: dict[str, float] = {}

    def h(self, name: str) -> Histogram:
        hist = self.hist.get(name)
        if hist is None:
            hist = self.hist[name] = Histogram()
        return hist

    def observe(self, name: str, v: int) -> None:
        self.h(name).record(v)

    def inc(self, name: str, by: int = 1) -> None:
        self.count[name] = self.count.get(name, 0) + by

    def set(self, name: str, v: float) -> None:
        self.gauge[name] = v

    def export(self) -> dict[str, object]:
        return {
            "worker": self.worker,
            "pid": self.pid,
            "exported_at": time.time(),
            "uptime_s": time.time() - self.started,
            "hist": {k: v.export() for k, v in self.hist.items()},
            "count": dict(self.count),
            "gauge": dict(self.gauge),
        }

    def dump(self, directory: str, stem: str = "worker") -> None:
        path = Path(directory) / f"{stem}-{self.worker}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.export()))
        tmp.replace(path)


def merge(exports: list[dict[str, object]]) -> dict[str, object]:
    hist: dict[str, dict[str, object]] = {}
    count: dict[str, int] = {}
    for ex in exports:
        for name, h in ex["hist"].items():  # type: ignore[union-attr]
            agg = hist.setdefault(name, {"n": 0, "sum": 0, "max": 0, "b": {}})
            agg["n"] += h["n"]  # type: ignore[operator]
            agg["sum"] += h["sum"]  # type: ignore[operator]
            agg["max"] = max(agg["max"], h["max"])  # type: ignore[type-var]
            b = agg["b"]
            for k, c in h["b"].items():
                b[k] = b.get(k, 0) + c  # type: ignore[union-attr]
        for name, c in ex["count"].items():  # type: ignore[union-attr]
            count[name] = count.get(name, 0) + c
    per_worker = [{"worker": ex.get("worker"), "pid": ex.get("pid"), "exported_at": ex.get("exported_at"),
                   "gauge": ex.get("gauge", {})} for ex in exports]
    return {"workers": len(exports), "hist": hist, "count": count, "per_worker": per_worker}


def summarize(hist: dict[str, dict[str, object]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for name, h in sorted(hist.items()):
        counts = [0] * _NBUCKETS
        for k, c in h["b"].items():  # type: ignore[union-attr]
            counts[int(k)] = c
        n = int(h["n"])  # type: ignore[call-overload]
        row = {"n": float(n), "max_ms": h["max"] / 1e6}  # type: ignore[operator]
        for q in QUANTILES:
            row[f"p{q * 100:g}_ms"] = quantile_of(counts, n, q, int(h["max"])) / 1e6  # type: ignore[call-overload]
        row["mean_ms"] = (h["sum"] / n / 1e6) if n else 0.0  # type: ignore[operator]
        out[name] = row
    return out


def _split(name: str) -> tuple[str, str]:
    """'shed{reason="guard"}' -> ('shed', 'reason="guard"'); plain names have no labels."""
    if "{" in name:
        base, labels = name.split("{", 1)
        return base, labels.rstrip("}")
    return name, ""


def prometheus(export: dict[str, object]) -> str:
    lines: list[str] = []
    summary = summarize(export["hist"])  # type: ignore[arg-type]
    for name, row in summary.items():
        for key, val in row.items():
            lines.append(f'rv_{name}{{stat="{key}"}} {val:.6f}')
    for name, c in sorted(export["count"].items()):  # type: ignore[union-attr]
        base, labels = _split(name)
        lines.append(f"rv_{base}_total{{{labels}}} {c}" if labels else f"rv_{base}_total {c}")
    for name, g in sorted(export.get("gauge", {}).items()):  # type: ignore[union-attr]
        base, labels = _split(name)
        lines.append(f"rv_{base}{{{labels}}} {g}" if labels else f"rv_{base} {g}")
    return "\n".join(lines) + "\n"
