# P5.17 — Combined B1/B2/B4 end-to-end verify

**Iteration:** cursor-ralph-iter15  
**Date:** 2026-07-02  
**Story:** P5.17 (scratchpad item 17)  
**Verdict:** **PASS** — 12/12 automated asserts

## Harness

`scripts/playwright_mcp_b1_b2_b4_e2e.mjs` → `mcp-parallel/findings/p5-17/`

## Results

| Assert | Result |
|--------|--------|
| E2E-B1-1 Linear stdio: no "no URL" error | PASS |
| E2E-B1-2 Linear: ≤1 Authorize button | PASS |
| E2E-B2-1 HTTP oauth registered 201 | PASS |
| E2E-B2-2 pending authorization badge | PASS |
| E2E-B2-3 Authorize to load tools (no N tools) | PASS |
| E2E-B2-4 exactly ONE Authorize | PASS |
| E2E-B2-5 Sync disabled pre-auth | PASS |
| E2E-B4 focus kept: name/url/description/stdio-command | PASS (24 keys each) |
| E2E-B4 no focus loss in any field | PASS |

## Out of scope (manual OAuth)

Tools-populate after authorize requires real provider OAuth (Linear consent).
Use `scripts/ralph/headed_browser.sh` + noVNC for manual authorize→tools flow.

## Next

- **P6.18:** pull Claude gateway+broker changes; integration check
- **P4.13:** still BLOCKED on Claude gateway wiring
