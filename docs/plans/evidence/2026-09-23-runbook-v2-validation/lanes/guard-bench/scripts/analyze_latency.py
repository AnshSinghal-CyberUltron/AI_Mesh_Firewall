#!/usr/bin/env python3
"""Recompute Exp A per-call latency tables from raw ns samples (lat_*.json from bench_latency_gpu.py),
Triton RTT (lat_rtt.json from triton_bench.py latency) and trtexec per-iteration exports.

usage: analyze_latency.py <evidence raw dir>   (walks A/, B/, C/ subtrees) -> latency_table.json + .csv
"""
import csv
import glob
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402


def model_of(path):
    for part in Path(path).parts:
        if part.startswith("Llama-Prompt-Guard-2-"):
            return part.replace("Llama-Prompt-Guard-2-", "PG2-")
    return "?"


def main():
    root = Path(sys.argv[1])
    rows = []
    for f in sorted(glob.glob(str(root / "A/**/lat_*.json"), recursive=True)):
        d = json.load(open(f))
        be = d["meta"]["backend"]
        tag = Path(f).stem.replace("lat_", "")
        for c in d["configs"]:
            h = C.summarize_ms(np.asarray(c["host_ns"]) / 1e6)
            dv = C.summarize_ms(np.asarray(c["devio_ns"]) / 1e6) if "devio_ns" in c else None
            rows.append({"exp": "A", "model": model_of(f), "backend": tag, "path": "in-process ORT " + be,
                         "W": c["W"], "seq": c["seq"], "n": h["n"], "p50": h["p50"], "p90": h["p90"], "p99": h["p99"],
                         "max": h["max"], "devio_p50": dv["p50"] if dv else None,
                         "cold_ready_s": c.get("cold_ready_s"), "finite": c.get("finite"),
                         "gpu_after": c.get("gpu_after"), "file": str(Path(f).relative_to(root))})
    for f in sorted(glob.glob(str(root / "*/**/lat_rtt.json"), recursive=True)):
        d = json.load(open(f))
        exp = Path(f).relative_to(root).parts[0]
        for r in d["rows"]:
            h = C.summarize_ms(np.asarray(r["raw_ns"]) / 1e6)
            rows.append({"exp": exp, "model": model_of(f), "backend": "triton_trt_plan", "path": f"Triton gRPC {d['url']}",
                         "W": r["W"], "seq": 512, "n": h["n"], "p50": h["p50"], "p90": h["p90"], "p99": h["p99"],
                         "max": h["max"], "file": str(Path(f).relative_to(root))})
    for f in sorted(glob.glob(str(root / "B/**/trtexec_times_W*.json"), recursive=True)):
        try:
            t = json.load(open(f))
        except Exception:
            continue
        W = int(Path(f).stem.split("_W")[-1])
        comp = np.asarray([x["computeMs"] for x in t])
        h = C.summarize_ms(comp)
        rows.append({"exp": "B-trtexec", "model": model_of(f), "backend": Path(f).parent.name, "path": "trtexec GPU compute only",
                     "W": W, "seq": 512, "n": h["n"], "p50": h["p50"], "p90": h["p90"], "p99": h["p99"], "max": h["max"],
                     "file": str(Path(f).relative_to(root))})
    (root / "latency_table.json").write_text(json.dumps(rows, indent=1))
    with open(root / "latency_table.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["exp", "model", "backend", "path", "W", "seq", "n", "p50", "p90", "p99", "max",
                                           "devio_p50", "cold_ready_s", "finite", "gpu_after", "file"], extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    for r in rows:
        print(f"{r['exp']:10s} {r['model']:8s} {r['backend']:24s} W={r['W']} seq={r['seq']:3d} n={r['n']:5d} "
              f"p50={r['p50']:8.3f} p90={r['p90']:8.3f} p99={r['p99']:8.3f} max={r['max']:8.3f}")


if __name__ == "__main__":
    main()
