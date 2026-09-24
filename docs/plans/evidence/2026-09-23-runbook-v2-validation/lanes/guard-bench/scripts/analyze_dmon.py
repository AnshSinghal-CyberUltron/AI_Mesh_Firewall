#!/usr/bin/env python3
"""Summarise `nvidia-smi dmon -s pucvmet -o T` logs: while the GPU is busy (sm% >= 50), the distribution of
board power, SM clock (pclk) and power-cap violation %, i.e. how hard the L4's 72 W limit throttles clocks.

usage: analyze_dmon.py <dmon.log> [...]
"""
import json
import sys

import numpy as np


def parse(path):
    cols, rows = None, []
    for line in open(path, errors="ignore"):
        if line.startswith("#Time"):
            cols = line[1:].split()
            continue
        if line.startswith("#") or not line.strip() or cols is None:
            continue
        parts = line.split()
        if len(parts) != len(cols):
            continue
        rows.append(dict(zip(cols, parts)))
    return rows


def num(v):
    try:
        return float(v)
    except ValueError:
        return float("nan")


def main():
    out = {}
    for p in sys.argv[1:]:
        rows = parse(p)
        busy = [r for r in rows if num(r.get("sm", "nan")) >= 50]
        if not busy:
            out[p] = {"samples": len(rows), "busy_samples": 0}
            continue
        pw = np.array([num(r["pwr"]) for r in busy])
        ck = np.array([num(r["pclk"]) for r in busy])
        pv = np.array([num(r.get("pviol", "nan")) for r in busy])
        tg = np.array([num(r.get("gtemp", "nan")) for r in busy])
        out[p] = {"samples": len(rows), "busy_samples": len(busy),
                  "power_W": {"p50": float(np.nanpercentile(pw, 50)), "max": float(np.nanmax(pw))},
                  "sm_clock_MHz": {"min": float(np.nanmin(ck)), "p10": float(np.nanpercentile(ck, 10)),
                                   "p50": float(np.nanpercentile(ck, 50)), "max": float(np.nanmax(ck))},
                  "power_violation_pct_mean": float(np.nanmean(pv)), "gpu_temp_C_max": float(np.nanmax(tg))}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
