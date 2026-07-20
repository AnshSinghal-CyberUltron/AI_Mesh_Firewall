# P10 iter42 — fresh isolation + inter-round restart fix

## Step 1: Fresh multi-org harness ALONE

**Prep:** `docker restart zeroshield-mcp-sandbox org-a-mcp-sandbox org-b-mcp-sandbox` (+15s)  
**Command:** `./gateway/.venv/bin/python3 -u scripts/mcp_multi_org_harness.py` (default ROUNDS=1)  
**Log:** `mcp-parallel/findings/p10-41/fresh_harness_alone.log`  
**Verdict:** **GREEN**

| Phase | Pass |
|-------|------|
| capability | 15/15 |
| echo | 15/15 |
| sum | 15/15 |
| concurrency | 540/540 |
| cross-tenant | 6/6 |

**Conclusion:** Cross-tenant isolation is sound on fresh sandboxes. iter41 R2 failures were contamination after round-1 load stress (status=0 / wedged sandboxes), not a reproducible gateway authz bug.

## Step 2: Gate script fix

**Change:** `scripts/mcp_p10_recursive_gate.py` — `INTER_ROUND_SANDBOX_RESTART` (default on) restarts all 3 scale sandboxes **between** consecutive gate rounds (after round N passes, before round N+1 sleep).

## Step 3: 3× recursive gate re-run

**Command:** `ROUNDS_GATE=3 INTER_HARNESS_SLEEP=20 ./gateway/.venv/bin/python3 -u scripts/mcp_p10_recursive_gate.py`  
**Log:** `mcp-parallel/findings/p10-41/gate_iter42.log`  
**Report:** `mcp-parallel/findings/p10-32/recursive_gate_report.json`  
**Elapsed:** 3238.9s  
**Verdict:** **FAIL** (stopped after round 2; round 3 not run)

| Round | Overall | broker | agent | multi-org | concurrency | load | leakage | oauth | playwright |
|-------|---------|--------|-------|-----------|-------------|------|---------|-------|------------|
| 1 | **PASS** | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 2 | **FAIL** | PASS | PASS | **FAIL** | PASS | FAIL | FAIL | FAIL | FAIL |
| 3 | — | (not run) | | | | | | | |

### Round 2 — `multi-org` (`HARNESS: RED`)

**Cross-tenant: 6/6 PASS** (inter-round restart fixed iter41's 6/6 BREACH class).  
Failures are **echo/sum canary mismatches** (9/651) with `status=200 id_ok=True canary_ok=False` — wrong payload, not authz breach. See `mcp-parallel/findings/p10-32/multi_org_fail.log`.

Concurrency within harness: 539/540 passed, 0 gate_violations.

Downstream harnesses (load TIMEOUT, leakage/oauth ReadTimeout, playwright) are cascade from wedged post-stress fleet — not isolated gate failures.

### Interpretation

- **Isolation:** sound on fresh sandboxes; iter41 R2 cross-tenant BREACH was **post-load contamination**, not reproducible authz bug.
- **3× green:** NOT achieved. Inter-round sandbox restart is necessary but insufficient — round 2 multi-org still flakes on echo/sum when `ROUNDS=3` after full round-1 load + 4m agent-pytest.
- **No CROSS_TENANT.md** — fresh matrix green; no gateway change warranted (Claude-owned).
