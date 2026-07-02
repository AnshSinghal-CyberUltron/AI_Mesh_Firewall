# P1.2 — HTTP OAuth server lists with 0 tools (B2)

**Date:** 2026-07-02  
**Agent:** cursor-ralph-iter2  
**Stack:** frontend :8180, control :8100, gateway :8300  

## Goal

Register `streamable-http` + `auth_type=oauth` (Linear MCP URL) and confirm the server **lists
immediately** with **0 tools** while still unauthorized — the misleading “ready” card (B2).

## Method

1. Joined hive `hive-1782976205971-u1gav1` as `cursor-ralph-iter2`; broadcast Cursor ownership of `frontend/**` + `sandbox-image/agent/**`.
2. Playwright: `scripts/playwright_mcp_p1_http_oauth_repro.mjs` with `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser` (host Ubuntu 26 lacks Playwright browser bundles).
3. Browser MCP cross-check on live stack (modal register path).

## Result: B2 **partial** — lists 0 tools while unauthorized; pending cues present

| Check | Expected (bug) | Observed |
|-------|----------------|----------|
| Server appears in list immediately (no authorize/sync) | yes | **yes** (`listedImmediately: true`, tab count 2→3) |
| `tools_count` / UI “0 tools” pre-auth | yes (misleading) | **yes** (backend `0`, UI `0 tools`) |
| `oauth_authorized` | false | **false** |
| Distinct “Pending authorization” state (not generic 0-tools card) | desired UX | **partial** — `authorization required` badge + disabled Sync + Authorize button |
| Tools API pre-auth | empty/gated | **200, count 0** |

UI card snippet (immediate post-register):

```
p1-2-http-oauth-mr3753tg
Unknown
authorization required
https://mcp.linear.app/mcp
0 tools
… Authorize (sync disabled)
```

Backend row: `connection_status: unknown`, `last_health_status: unreachable`, `last_sync_at: null`.

## Verdict flags (from repro script)

- `b2ListsZeroToolsWhileUnauthorized`: **true**
- `b2BugPartial`: **true** (pending cues present)
- `b2BugConfirmed`: **false** (not a “ready-looking” card with zero pending cues)

## Artifacts

- Screenshots: `mcp-parallel/findings/p1-2/01-mcp-panel-before.png`, `02-after-modal-register.png`, `03-immediate-list-state.png`
- JSON report: `mcp-parallel/findings/p1-2/report.json`
- Network trace: `mcp-parallel/findings/p1-2/network.jsonl` (16 mcp-connector/oauth lines)
- Repro gate: `scripts/playwright_mcp_p1_http_oauth_repro.mjs` → **PASS** (exit 0)

## Conclusion

HTTP OAuth servers **do** list immediately with **0 tools** before authorization — core B2 symptom
confirmed. UI already surfaces `authorization required` + Authorize + disabled Sync, so P5.15 should
focus on a **distinct pending state** (not re-adding badges) and blocking tool sync until
`oauth_authorized`. Probe servers cleaned up after capture.
