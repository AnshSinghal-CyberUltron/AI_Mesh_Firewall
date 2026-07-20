#!/usr/bin/env python3
"""Portable stdlib load tester + docker-stats CPU sampler.

No third-party deps (urllib + threads), so it runs from the host against the
published ports and works identically in every profile used for the P7 proof.

Measures RPS / p50 / p95 / p99 / errors for a URL under N concurrent clients for
D seconds, while sampling ``docker stats`` to report peak CPU% (=> cores used)
and memory per container. Emits a JSON report to stdout (and optionally a file).

Usage:
  python3 scripts/perf/loadtest.py --url http://127.0.0.1:8100/api/health/ \
      --concurrency 100 --duration 12 --containers ai_mesh_firewall-control-1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
import urllib.request


def _worker(url, method, timeout, stop_at, lat, errs, codes, lock):
    local_lat = []
    local_err = 0
    local_codes = {}
    while time.monotonic() < stop_at:
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                r.read()
                c = r.status
            local_lat.append(time.monotonic() - t0)
            local_codes[c] = local_codes.get(c, 0) + 1
        except Exception as exc:  # noqa: BLE001 - any failure counts as an error
            local_err += 1
            key = type(exc).__name__
            local_codes[key] = local_codes.get(key, 0) + 1
    with lock:
        lat.extend(local_lat)
        errs[0] += local_err
        for k, v in local_codes.items():
            codes[k] = codes.get(k, 0) + v


def _parse_cpu(perc: str) -> float:
    try:
        return float(perc.strip().rstrip("%"))
    except ValueError:
        return 0.0


def _sample_stats(containers, stop_at, peaks, lock):
    fmt = "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"
    while time.monotonic() < stop_at:
        try:
            out = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", fmt],
                capture_output=True, text=True, timeout=15,
            ).stdout
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
            continue
        for line in out.strip().splitlines():
            parts = line.split("|")
            if len(parts) < 3:
                continue
            name, perc, mem = parts[0], parts[1], parts[2]
            if containers and name not in containers:
                continue
            cpu = _parse_cpu(perc)
            with lock:
                cur = peaks.get(name, {"cpu_pct": 0.0, "mem": mem})
                if cpu > cur["cpu_pct"]:
                    cur["cpu_pct"] = cpu
                cur["mem"] = mem
                peaks[name] = cur
        time.sleep(0.5)


def _pct(sorted_lat, q):
    if not sorted_lat:
        return None
    idx = min(len(sorted_lat) - 1, int(round(q * (len(sorted_lat) - 1))))
    return round(sorted_lat[idx] * 1000, 2)  # ms


def _load_phase(url, concurrency, method, timeout, stop_at):
    """Run `concurrency` threads hammering url until stop_at; return (lat[], errs, codes)."""
    lat = []
    errs = [0]
    codes = {}
    lock = threading.Lock()
    threads = [
        threading.Thread(target=_worker,
                         args=(url, method, timeout, stop_at, lat, errs, codes, lock))
        for _ in range(concurrency)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return lat, errs[0], codes


def _child_entry(args_tuple):
    """multiprocessing entry: run one generator process, return a compact summary."""
    url, concurrency, method, timeout, stop_at = args_tuple
    lat, err, codes = _load_phase(url, concurrency, method, timeout, stop_at)
    lat.sort()
    return {
        "ok": len(lat),
        "err": err,
        "codes": codes,
        "p50": _pct(lat, 0.50),
        "p95": _pct(lat, 0.95),
        "p99": _pct(lat, 0.99),
        "max": round(lat[-1] * 1000, 2) if lat else None,
    }


def run(url, concurrency, duration, method, timeout, containers, warmup, procs=1):
    # Warmup (not measured).
    for _ in range(warmup):
        try:
            urllib.request.urlopen(url, timeout=timeout).read()
        except Exception:  # noqa: BLE001
            pass

    peaks = {}
    lock = threading.Lock()
    stop_at = time.monotonic() + duration
    # Sampler runs in the parent for the whole window (measures the TARGET's cores).
    sampler = threading.Thread(target=_sample_stats, args=(containers, stop_at, peaks, lock))
    t0 = time.monotonic()
    sampler.start()

    if procs <= 1:
        lat, err, codes = _load_phase(url, concurrency, method, timeout, stop_at)
        lat.sort()
        child_summaries = [{
            "ok": len(lat), "err": err, "codes": codes,
            "p50": _pct(lat, 0.50), "p95": _pct(lat, 0.95), "p99": _pct(lat, 0.99),
            "max": round(lat[-1] * 1000, 2) if lat else None,
        }]
    else:
        import multiprocessing as mp
        payload = (url, concurrency, method, timeout, stop_at)
        with mp.Pool(procs) as pool:
            child_summaries = pool.map(_child_entry, [payload] * procs)

    sampler.join()
    elapsed = time.monotonic() - t0

    # Aggregate across generator processes.
    ok = sum(c["ok"] for c in child_summaries)
    err = sum(c["err"] for c in child_summaries)
    total = ok + err
    codes = {}
    for c in child_summaries:
        for k, v in c["codes"].items():
            codes[k] = codes.get(k, 0) + v
    # Latency: worst (max) percentile across generators — conservative.
    def _worst(key):
        vals = [c[key] for c in child_summaries if c[key] is not None]
        return max(vals) if vals else None

    report = {
        "url": url,
        "concurrency": concurrency,
        "procs": procs,
        "total_clients": concurrency * procs,
        "duration_s": round(elapsed, 2),
        "requests": total,
        "ok": ok,
        "errors": err,
        "rps": round(total / elapsed, 1) if elapsed else 0,
        "p50_ms": _worst("p50"),
        "p95_ms": _worst("p95"),
        "p99_ms": _worst("p99"),
        "max_ms": _worst("max"),
        "codes": codes,
        "containers": {
            name: {"peak_cpu_pct": round(v["cpu_pct"], 1),
                   "cores_used": round(v["cpu_pct"] / 100, 2),
                   "mem": v["mem"]}
            for name, v in peaks.items()
        },
    }
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--duration", type=float, default=12)
    ap.add_argument("--method", default="GET")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--procs", type=int, default=1,
                    help="generator processes (bypass the Python GIL for higher offered load)")
    ap.add_argument("--containers", nargs="*", default=[])
    ap.add_argument("--out", default=None)
    ap.add_argument("--label", default=None)
    args = ap.parse_args(argv)

    report = run(args.url, args.concurrency, args.duration, args.method,
                 args.timeout, set(args.containers), args.warmup, procs=args.procs)
    if args.label:
        report["label"] = args.label
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
