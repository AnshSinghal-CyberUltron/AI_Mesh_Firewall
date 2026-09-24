#!/usr/bin/env python3
"""Old (pre-READY analyze.py c9f89fe8, xrv fields from the adapter) vs READY (analyze.py 3874eaac, v1 extractor)
per run: qualified, infra errors, policy strata, T_fw_addon p50/p99, verdict. usage: compare_ready.py RUN..."""
import json, sys
from pathlib import Path
E = Path("/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs")
def g(d, *k):
    for x in k:
        d = d.get(x) if isinstance(d, dict) else None
    return d
def f(x, nd=1):
    return "-" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))
print("| run | qualified old / READY | infra err old / READY (rate) | policy blocks (FP) READY | T_fw p50 old / READY | T_fw p99 old / READY | verdict old / READY | READY failing checks |")
print("|---|---|---|---|---|---|---|---|")
for run in sys.argv[1:]:
    o = json.loads((E / run / "analysis" / "summary.json").read_text())
    r = json.loads((E / run / "analysis-ready" / "summary.json").read_text())
    pol = r.get("policy") if isinstance(r.get("policy"), dict) else {}
    ie = r.get("infra_errors", r.get("errors"))
    fails = [k for k, v in (g(r, "verdict", "checks") or {}).items() if v is False]
    print(f"| {run} | {o.get('qualified')} / {r.get('qualified')} | {o.get('errors')} / {ie} ({f(r.get('error_rate'),4)}) | "
          f"{pol.get('blocks_total', '-')} ({pol.get('false_positive_blocks', '-')}) | {f(g(o,'T_fw_addon','p50'))} / {f(g(r,'T_fw_addon','p50'))} | "
          f"{f(g(o,'T_fw_addon','p99'))} / {f(g(r,'T_fw_addon','p99'))} | "
          f"{'PASS' if g(o,'verdict','pass') else 'FAIL'} / {'PASS' if g(r,'verdict','pass') else 'FAIL'} | {', '.join(fails) or '-'} |")
