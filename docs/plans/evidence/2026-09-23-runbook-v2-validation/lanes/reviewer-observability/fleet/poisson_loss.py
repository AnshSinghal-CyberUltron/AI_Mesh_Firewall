#!/usr/bin/env python3
"""Per-worker guard admission (overload.py take_guard: a 2nd guard request on a worker is shed whenever the
~523-token cap is occupied) = single-server loss system. Erlang-B(1): B = rho/(1+rho), rho = lambda_worker * S,
S = guard hold time (det scan + guard wait, mean ~4.35 ms in f1u-070). Compare with the lane's Poisson runs."""
import json
from pathlib import Path
FO = Path(__file__).resolve().parent
rec = {json.loads(l)["run"]: json.loads(l) for l in open(FO / "reconcile.jsonl")}
S = 0.00435
for run, workers in (("f8-020-poisson", 6), ("f1u-070-poisson", 18), ("f8-060-poisson", 6)):
    d = rec[run]; adm = d["gw_admitted"]; shed = d["gw_shed"]
    rate = adm / (30 / 2 + 60 + 300)  # all-phase admitted / (ramp-average + warm-up + measure) seconds
    lw = rate / workers; rho = lw * S; b = rho / (1 + rho)
    print(f"{run}: lambda/worker {lw:.2f}/s rho {rho:.4f} ErlangB {100*b:.2f}% measured {100*shed/adm:.2f}% ratio {shed/adm/b:.2f}")
for f in (1.6, 1.9):
    lw = 0.001 / f / S
    print(f"0.1% shed under Poisson needs lambda/worker <= {lw:.3f}/s (x{f} imbalance) -> g2-8 (6 w) {6*lw:.2f} RPS, g2-24 (18 w) {18*lw:.2f} RPS")
