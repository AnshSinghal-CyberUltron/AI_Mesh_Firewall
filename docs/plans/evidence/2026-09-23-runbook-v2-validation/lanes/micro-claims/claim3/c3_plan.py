#!/usr/bin/env python3
"""Adaptive claim-3 ladder. A ladder is stopped at its first FAIL (p99 >= 5 ms, >0.1 % errors, or schedule drops);
the knee (highest PASS) is then repeated to 3 total runs. Each step = c3_step.sh (5-min measured, open-loop)."""
import json, os, subprocess, sys
E = "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/micro-claims"
STEP = f"{E}/claim3/c3_step.sh"
def run(label, cfg, rate):
    subprocess.run([STEP, label, cfg, str(rate)], check=False)
    try: return json.load(open(f"{E}/claim3/runs/{label}/done.json"))["pass_p99_lt_5ms"]
    except Exception: return None
def ladder(cfg, rates, stop_on_fail=True):
    knee, first_fail = None, None
    for r in rates:
        ok = run(f"c3_{cfg}_r{r}", cfg, r)
        print(f"[plan] {cfg} r={r} pass={ok}", flush=True)
        if ok: knee = r
        else:
            first_fail = r
            if stop_on_fail: break
    return knee, first_fail
res = {}
res["A_prod"] = ladder("A_prod", [200, 400, 450, 500, 1000], stop_on_fail=False)
res["B_keepalive"] = ladder("B_keepalive", [400, 1000, 5000, 10000, 20000, 30000, 40000, 50000])
for cfg in ("A_prod", "B_keepalive"):
    knee = res[cfg][0]
    if knee:
        for rep in (2, 3):
            ok = run(f"c3_{cfg}_r{knee}_rep{rep}", cfg, knee); print(f"[plan] {cfg} knee r={knee} rep{rep} pass={ok}", flush=True)
for r in (400, 5000, 20000, 40000):
    ok = run(f"c3_direct_r{r}", "direct", r); print(f"[plan] direct r={r} pass={ok}", flush=True)
json.dump(res, open(f"{E}/claim3/plan_result.json", "w")); print("PLAN DONE", res, flush=True)
