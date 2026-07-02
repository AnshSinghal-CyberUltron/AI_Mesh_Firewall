# P6.19 prep — P1 repro re-verify (iter16)

**Date:** 2026-07-02  
**Context:** P6.18 blocked; ran P1 regression gates per fallback strategy.

## Results

| ID | Bug | Gate | Result |
|----|-----|------|--------|
| B1 | OAuth+stdio / duplicate Authorize / no-URL error | `playwright_mcp_b1_b2_b4_e2e.mjs` | **PASS** (12/12) |
| B2 | HTTP oauth lists 0-tools before auth | same | **PASS** — amber "Pending authorization" |
| B4 | Add-Server dialog focus loss | same | **PASS** — 24 keys × 5 fields |
| B3 | Sandbox temporarily unavailable | not re-run (requires broker stop) | Prior warm fix (iter P5.19) unchanged |

## P4.13 status

**Still BLOCKED** — gateway routes HTTP/SSE direct to upstream. See `mcp-parallel/findings/p4-13/BLOCKER.md` and `mcp-parallel/findings/p6-18/BLOCKER.md`.

## Conclusion

Three of four original UI bugs remain verified fixed. End-to-end sandbox transport verification (P4.13) cannot proceed until Claude lands gateway wiring.
