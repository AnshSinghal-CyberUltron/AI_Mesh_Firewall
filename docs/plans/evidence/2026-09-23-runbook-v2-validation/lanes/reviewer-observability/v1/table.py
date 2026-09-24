#!/usr/bin/env python3
"""Tabulate recompute.py outputs for v1 runs: PUB (lane definition), C (infra=+inf), ABC (rules a,b,c on the
fnv1a64(rid)%10 subsample), per-stratum p99s, wire vs audit qualification."""
import json, sys
from pathlib import Path
O = Path(__file__).resolve().parent / "out"
def g(d, *ks):
    for k in ks:
        d = d.get(k) if isinstance(d, dict) else None
    return d
def f(x):
    if x is None: return "-"
    if x == float("inf") or x == "Infinity": return "inf"
    return f"{x:.1f}" if isinstance(x, float) else str(x)
print("| run | src | offered | qual | FP blk | infra | PUB T_fw p50/p99 | PUB JSON p99 | PUB SSE p99 | SSE first-byte p99 | C T_fw p99 | ABC T_fw p99 (n) | ABC first_tok1 p50/p99 | ABC lag p50/p99 | nohold p99 / C-nohold p99 |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for run in sys.argv[1:]:
    for src in ("audit", "wire"):
        p = O / f"{run}.{src}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        c = d["categories"]
        print(f"| {run} | {src} | {d['offered']} | {c.get('qualified',0)} | {c.get('block_fp',0)} | {c.get('infra',0)} | "
              f"{f(g(d,'PUB_T_fw_addon','p50'))}/{f(g(d,'PUB_T_fw_addon','p99'))} | {f(g(d,'PUB_json','p99'))} | {f(g(d,'PUB_sse','p99'))} | "
              f"{f(g(d,'PUB_first_byte_sse','p99'))} | {f(g(d,'C_T_fw_addon','p99'))} | {f(g(d,'ABC_T_fw_addon','p99'))} ({g(d,'ABC_T_fw_addon','n')}) | "
              f"{f(g(d,'sample_first_tok1_sse','p50'))}/{f(g(d,'sample_first_tok1_sse','p99'))} | {f(g(d,'sample_lag','p50'))}/{f(g(d,'sample_lag','p99'))} | "
              f"{f(g(d,'PUB_nohold_T_addon_total','p99'))} / {f(g(d,'C_nohold','p99'))} |")
