# P1.1 — stdio Linear OAuth repro (B1)

**Date:** 2026-07-02  
**Agent:** cursor-ralph-iter1  
**Stack:** frontend :8180, control :8100, gateway :8300, mcp-broker :8311  

## Goal

Reproduce B1: register Linear (stdio + mcp-remote) and capture **two** Authorize buttons plus
`OAuth authorize failed: Server has no URL; OAuth is only for HTTP transports`.

## Method

1. `docker compose --profile services up -d` (+ mcp-broker build)
2. `manage.py migrate` + `ensure_zeroshield_admin`
3. Playwright via `mcr.microsoft.com/playwright:v1.60.0-noble` (host Ubuntu 26 lacks Playwright browsers)
4. Scripts: `scripts/playwright_mcp_p1_stdio_oauth_repro.mjs`, `tests/e2e/mcp_sandbox_adversarial/mcp_oauth_transport_ui.mjs`

## Result: B1 dup-button / no-URL error **NOT reproduced**

| Check | Expected (bug) | Observed |
|-------|----------------|----------|
| Authorize buttons on Linear card | 2 | **1** |
| "Server has no URL" error | visible | **absent** |
| stdio modal offers `oauth` auth type | yes (bug) | **hidden** (`mcp_oauth_transport_ui` PASS) |
| Control HTTP OAuth button on stdio | visible (bug) | **absent** (`serverUsesHttpOAuth` guard) |

Linear card shows:
- `authorization required` badge (B2-related pending state — partial)
- **one** gateway-side `Authorize` button (`serverNeedsOAuth` → `startOAuth`)
- `auth_type: none`, `transport: stdio`, `args` includes `mcp-remote`

## Separate issue (not B1)

Clicking Authorize posts to production gateway URL and returns **401 unauthorized**:
`https://aimeshgateway.zeroshield.ai/gateway/zeroshield/mcp/linear-mcp/oauth/start`

Likely env: local stack resolves gateway base to prod URL without a valid org gateway key in browser context.

## Artifacts

- Screenshots: `mcp-parallel/findings/p1-1/*.png`
- JSON report: `mcp-parallel/findings/p1-1/report.json`
- Regression gate: `mcp_oauth_transport_ui.mjs` → **PASS**

## Conclusion

Prior B1 frontend fixes (`serverNeedsOAuth` / `serverUsesHttpOAuth` mutual exclusion, modal oauth filter)
appear effective. Item P5.14 should be **verification-first**, not blind re-implementation. Next: P1.2 (HTTP oauth 0-tools card / B2).
