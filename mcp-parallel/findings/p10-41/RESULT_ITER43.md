# P10 iter43 — timing stabilization + ROUNDS=1 multi-org

## Script changes (commit `25ec75f8`)

**File:** `scripts/mcp_p10_recursive_gate.py` only

| Knob | Before | After |
|------|--------|-------|
| `INTER_HARNESS_SLEEP` | 8 | **20** |
| `INTER_ROUND_SLEEP` | 45 | **30** |
| `POST_RESTART_SETTLE` | (none) | **30** after inter-round sandbox restart |
| multi-org `ROUNDS` | 3 | **1** (concurrency sub-gate stress-tests) |

Rationale (iter42): R2 multi-org `HARNESS: RED` was echo/sum `canary_ok=False` after R1 full load with `ROUNDS=3`; cross-tenant 6/6 stayed green. Hypothesis: reduce harness rounds + longer post-restart settle.

## Prep

```bash
docker restart zeroshield-mcp-sandbox org-a-mcp-sandbox org-b-mcp-sandbox
sleep 15
```

## Run

```bash
ROUNDS_GATE=3 ./gateway/.venv/bin/python -u scripts/mcp_p10_recursive_gate.py
```

**Log:** `mcp-parallel/findings/p10-41/gate_iter43.log`  
**Report:** `mcp-parallel/findings/p10-32/recursive_gate_report.json`  
**Elapsed:** 2560.4s (~43 min)  
**Verdict:** **FAIL** — stopped after round 1; rounds 2–3 not run

## Per-round results

| Round | Overall | broker | agent | multi-org | concurrency | load | leakage | oauth | playwright |
|-------|---------|--------|-------|-----------|-------------|------|---------|-------|------------|
| 1 | **FAIL** | PASS | PASS | **TIMEOUT** | **TIMEOUT** | **FAIL** | PASS | **FAIL** | PASS |
| 2 | — | (not run) | | | | | | | |
| 3 | — | (not run) | | | | | | | |

**Score: 0/3 consecutive all-green**

### Round 1 detail

- **broker-pytest:** 155 passed (2.32s)
- **agent-pytest:** 66 passed (249.92s / ~4m)
- **multi-org:** `TIMEOUT` (900s harness cap; no `HARNESS:` verdict — fleet likely wedged post agent-pytest + sandbox restart). Fail log not refreshed (stale iter42 RED artifact in `p10-32/multi_org_fail.log`).
- **concurrency:** `TIMEOUT` (900s). Partial fail log shows 376/960 received, 584 drops — cascade from wedged fleet, not isolation breach (`xtenant=0`).
- **load:** `TypeError: '>' not supported between instances of 'int' and 'NoneType'` at `mcp_load_live.py:195` (`base_pids=None` on all three org sandboxes). Harness bug / env flake, not gate-timing.
- **leakage:** PASS (26/26)
- **oauth:** `OAUTH_TRANSPORT: FAIL (84/85 checks)` — single check failure under post-stress fleet (see `p10-32/oauth_fail.log`).
- **playwright:** ALL PASS

### Interpretation

iter43 script tuning did **not** achieve 3× green. This run regressed vs iter42 R1 (which was all-green): multi-org never completed within the 900s timeout instead of finishing with echo/sum flakes. Likely causes:

1. **Fleet wedged** after 4m agent-pytest + immediate sandbox restart (only 15s `POST_SANDBOX_RESTART_SLEEP` before multi-org).
2. **Load harness** pre-existing `base_pids=None` TypeError when cgroup read fails.
3. Downstream TIMEOUT/FAIL on concurrency/oauth are **cascade**, not independent regressions.

### Next steps (not in iter43 scope)

- Increase `POST_SANDBOX_RESTART_SLEEP` before multi-org (or reuse `POST_RESTART_SETTLE` there).
- Fix `mcp_load_live.py` `peak_pids` None-guard.
- Re-run fresh harness alone after restart to confirm fleet health before full gate.
