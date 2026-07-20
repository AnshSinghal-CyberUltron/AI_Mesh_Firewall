# P5.15 — B2 verify: distinct "Pending authorization" state

**Iteration:** cursor-ralph-iter14  
**Date:** 2026-07-02  
**Story:** P5.15 (scratchpad item 15)  
**Verdict:** **PASS** — verification-first; no new frontend code required

## Context

P1.2 found B2 **partial**: freshly-registered HTTP oauth servers listed with 0 tools while
`oauth_authorized=false`. Pending cues existed (`authorization required`, Authorize, disabled Sync)
but the card also showed grey "Unknown" + "0 tools", reading like a normal empty server.

Commit `04748af5` already landed the fix in `MCPConnectorPanel.jsx` `renderServerCard`:
- `awaitingAuth = !srv.needs_reauth && serverAwaitingAuth(srv)`
- Primary badge → amber **"Pending authorization"** (replaces Unknown/conn badge)
- Tools line → **"Authorize to load tools"** (replaces numeric "N tools")
- Sync remains disabled via `syncBlockedForAuth`

## Playwright verification

**Harness:** `scripts/playwright_mcp_b2_verify.mjs`  
**Output:** `mcp-parallel/findings/p5-15/` (screenshot `01-pending-card.png`, `report.json`)

| Assert | Result |
|--------|--------|
| B2-1 distinct "Pending authorization" badge | PASS |
| B2-2a "Authorize to load tools" (no numeric tools count) | PASS |
| B2-2b NOT Connected/Unknown ready card | PASS |
| B2-3 Sync button DISABLED pre-auth | PASS |
| B2-4 exactly ONE Authorize button | PASS |
| B2-5 backend `tools_count=0`, `oauth_authorized=false` | PASS |

**Register:** HTTP `streamable-http` + `oauth` → `https://example.com/mcp` → 201  
**Card snippet:** `Pending authorization` + `Authorize to load tools` + single Authorize + sync disabled.

## Frontend gates

- `npm run lint` (unit tests): **26/26 PASS**
- `npm run build`: **PASS** (via `docker exec ai_mesh_firewall-frontend-1 npm run build`)

## Structural guarantee

Tools appear only after **both** `oauth_authorized=true` (control OAuth callback stores token) **and**
manual/auto sync (`_resync_server_tools`). Pre-auth sync is blocked in UI (`syncBlockedForAuth`) and
backend creates tool rows only on sync — no premature tool list.

## Next

- **P5.16:** B4 dialog focus — P1.3 already verified not reproducing; scratchpad item 16 still open for
  end-to-end authorize→tools populate flow.
- **P4.13:** still BLOCKED on Claude gateway wiring.
