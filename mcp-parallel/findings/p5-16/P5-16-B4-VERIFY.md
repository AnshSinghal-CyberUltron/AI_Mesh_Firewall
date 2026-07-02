# P5.16 — B4 verify: Add-Server dialog focus stability

**Iteration:** cursor-ralph-iter15  
**Date:** 2026-07-02  
**Story:** P5.16 (scratchpad item 16)  
**Verdict:** **PASS** — verification-first; no new frontend code required

## Context

P1.3 found B4 **NOT reproducing**: focus kept for 24 keystrokes across 5 dialog fields.
Prior fix in `Dialog.jsx` (`onCloseRef` + stable `handleKey` useCallback; effect deps
`[open, handleKey]` only) prevents focus-trap effect re-running per keystroke.
`MCPConnectorPanel` has no inline component definitions in render.

## Playwright verification

**Harness:** `scripts/playwright_mcp_b4_verify.mjs`  
**Output:** `mcp-parallel/findings/p5-16/` (screenshots 01–08, `report.json`)

| Assert | Result |
|--------|--------|
| B4-F focus kept: name (24 keys) | PASS |
| B4-F focus kept: url (24 keys) | PASS |
| B4-F focus kept: description (24 keys) | PASS |
| B4-F focus kept: stdio-command (24 keys) | PASS |
| B4-F focus kept: bearer-token (24 keys) | PASS |
| B4-ALL no focus loss in any field | PASS |
| B4-P1 GitHub preset prefills name | PASS |
| B4-P2 preset prefills url | PASS |
| B4-P3 preset transport=streamable-http | PASS |
| B4-P4 preset auth_type=bearer | PASS |
| B4-P5 focus retained on prefilled field (37 keys) | PASS |

**Result:** `b4BugConfirmed=false`, `b4BugNotReproducing=true`

## Frontend gates

- `npm run lint` (unit tests): **26/26 PASS**
- `npm run build` (docker): **PASS**

## Root cause (already fixed)

`Dialog.jsx` focus-trap effect previously depended on unstable `onClose` callback,
re-running on every parent render → input remount → focus loss after first keystroke.
Fix: `onCloseRef` pattern + `useCallback` for `handleKey` with stable deps.

## Next

- **P5.17:** combined B1/B2/B4 end-to-end Playwright verify
- **P4.13:** still BLOCKED on Claude gateway wiring
