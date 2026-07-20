# P10 iter44 — revert ROUNDS=3 + load base_pids guard

## Script changes

| File | Change |
|------|--------|
| `scripts/mcp_p10_recursive_gate.py` | Revert multi-org `ROUNDS` **1 → 3** (iter43 `25ec75f8` regression). Keep `INTER_ROUND_SANDBOX_RESTART` + `POST_RESTART_SETTLE=30`. |
| `scripts/mcp_load_live.py` | Guard `base_pids=None` when host cgroup `pids.current` is unreadable — skip pids peak/leak checks instead of `TypeError` on `max(int, None)`. |

## Prep

```bash
docker restart zeroshield-mcp-sandbox org-a-mcp-sandbox org-b-mcp-sandbox
sleep 15
```

## Smoke test (R1 only)

```bash
ROUNDS_GATE=1 ./gateway/.venv/bin/python -u scripts/mcp_p10_recursive_gate.py
```

**Log:** `mcp-parallel/findings/p10-41/gate_iter44_r1.log`  
**Report:** `mcp-parallel/findings/p10-32/recursive_gate_report.json`  
**Elapsed:** 662.5s (~11 min)

## Per-round results (R1)

| Gate | iter43 R1 | iter44 R1 |
|------|-----------|-----------|
| broker-pytest | PASS | PASS (157) |
| agent-pytest | PASS | PASS (67, ~4m) |
| multi-org | **TIMEOUT** | **PASS** (`HARNESS: GREEN`, ROUNDS=3) |
| concurrency | **TIMEOUT** | **PASS** |
| load | **FAIL** (`base_pids=None` TypeError) | **PASS** |
| leakage | PASS | PASS (26/26) |
| oauth | FAIL (84/85) | FAIL (84/85) — pre-existing |
| playwright | PASS | PASS |

**Verdict:** iter43 regressions **restored** (multi-org + load + concurrency). OAuth still fails standalone on `httpoauth.authorized_clean[zeroshield/linear-manual-oauth]` — authorized HTTP-oauth server with `tools=0 needs_reauth=False` (fleet data / B2 falsely-authorized-empty); same 84/85 as iter43, not introduced by this fix.

## Interpretation

- Reverting multi-org to `ROUNDS=3` + the load `None`-guard fixes the two iter43-specific R1 failures.
- OAuth 84/85 is orthogonal (persistent fleet state); iter42 R1 was all-green — may need a re-sync or harness tolerance review in a follow-up iteration.

## Next

- Full `ROUNDS_GATE=3` gate when ready (~40 min).
- Investigate `zeroshield/linear-manual-oauth` authorized-but-0-tools state for oauth 85/85.
