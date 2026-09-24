#!/usr/bin/env python3
"""Claim-3 plan, part 2 (replaces the first planner after its B r20000 step was marked FAIL only because of loadgen
schedule drops: 382 drops from two 13-17 ms loadgen VM stalls, p99 335 us, 0 errors). Classification per step:
  PASS        p99 < 5 ms, errors <= 0.1 %, 0 drops
  FAIL_SUT    p99 >= 5 ms or errors > 0.1 %                    -> ladder stops (first failing rate)
  INVALID_LG  only schedule drops > 0 (instrument, not SUT)    -> retried (label suffix _tryN), max 2 retries
The knee (highest PASS) is repeated to 3 PASS runs. All attempts are kept and reported."""
import json, subprocess, time
E = "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/micro-claims"
STEP = f"{E}/claim3/c3_step.sh"
while subprocess.run(["pgrep", "-f", "claim3/c3_" + "step.sh"], capture_output=True).stdout.strip():
    time.sleep(10)                                  # never overlap with a step still running
def classify(label):
    r = json.load(open(f"{E}/claim3/runs/{label}/done.json")); s = r["olg"]; L = s["latency_us"]; n = s["n_measured"]
    if L.get("p99", 1e12) >= 5000 or (n - s["ok"]) > 0.001 * n: return "FAIL_SUT"
    if s["drops_gt5ms"] > 0: return "INVALID_LG"
    return "PASS"
def run(base, cfg, rate, retries=2):
    for k in range(retries + 1):
        label = base if k == 0 else f"{base}_try{k + 1}"
        subprocess.run([STEP, label, cfg, str(rate)], check=False)
        c = classify(label); print(f"[plan2] {label} -> {c}", flush=True)
        if c != "INVALID_LG": return c
    return "INVALID_LG"
res = {}
res["A_prod_rep3"] = run("c3_A_prod_r400_rep3", "A_prod", 400)
knee, first_non_pass = 10000, None
for rate in (20000, 30000, 40000, 50000):
    base = f"c3_B_keepalive_r{rate}" + ("_retry" if rate == 20000 else "")
    c = run(base, "B_keepalive", rate)
    if c == "PASS": knee = rate
    else: first_non_pass = [rate, c]; break
res["B_keepalive"] = {"knee": knee, "first_non_pass": first_non_pass}
for rep in (2, 3):
    res[f"B_rep{rep}"] = run(f"c3_B_keepalive_r{knee}_rep{rep}", "B_keepalive", knee)
for r in (400, 5000, 20000, 40000):
    res[f"direct_{r}"] = run(f"c3_direct_r{r}", "direct", r, retries=1)
json.dump(res, open(f"{E}/claim3/plan2_result.json", "w")); print("PLAN2 DONE", res, flush=True)
