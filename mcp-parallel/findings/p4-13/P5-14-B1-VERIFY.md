# P5.14 — B1 OAuth UX (fallback from P4.13 block)

**Iteration:** cursor-ralph-iter13  
**Date:** 2026-07-02

## Verdict

**VERIFIED** — B1 UX invariants hold; no frontend code change required this iteration.

## Playwright gate

`scripts/playwright_mcp_b1_verify.mjs` → **8/8 PASS**

Artifacts: `p5-14-b1-verify/*.png`, `report.json`

| Assert | Result |
|--------|--------|
| Linear stdio: no "Server has no URL" | PASS |
| Linear stdio: ≤1 Authorize button | PASS (1 gateway button) |
| Linear stdio: click no no-URL error | PASS |
| HTTP oauth: register 201 | PASS |
| HTTP oauth: exactly ONE Authorize | PASS |
| HTTP oauth: popup opens | PASS |
| HTTP oauth: Pending authorization badge | PASS |
| HTTP oauth: no "Server has no URL" | PASS |

## B1 checklist vs code

| Requirement | Status | Location |
|-------------|--------|----------|
| OAuth selectable ONLY for HTTP transports | ✅ | `MCPConnectorPanel.jsx` AUTH_OPTIONS filter `:1496-1500` |
| Block oauth+stdio in form | ✅ | transport onChange drops oauth→none `:1401-1404` |
| ONE Authorize button per card | ✅ | `serverNeedsOAuth` vs `serverUsesHttpOAuth` mutually exclusive `:1179-1210` |
| Remove dup/broken control path | ⚠️ **Deferred** | See below |

## Why control authorize path remains (frontend-only constraint)

`startControlOAuth` → control `POST …/oauth/authorize/` is the **only** path that:

1. Runs RFC 9728/8414 discovery + DCR in control plane
2. Stores encrypted token in `MCPServerRegistration.auth_token`
3. Flips `oauth_authorized` so tool sync (`_resync_server_tools`) forwards bearer to gateway

Gateway `oauth/start` stores tokens **gateway-side only** (`mcp_oauth_proxy._token_save`). Control discover-tools (`views.py:442-450`) requires `server.auth_token` for `auth_type=oauth`. Removing `startControlOAuth` without a Claude-owned token bridge would break HTTP oauth tool sync.

**Not a duplicate button** — stdio mcp-remote uses gateway `startOAuth`; HTTP oauth uses control `startControlOAuth`. Conditions are mutually exclusive.

## Recommended follow-up (Claude-owned)

Unify HTTP oauth onto gateway `oauth/start` + add control callback/webhook to persist `auth_token` and set `oauth_authorized` — then frontend can delete `startControlOAuth`.

## Frontend gates

- `npm run lint` (unit tests)
- `npm run build`
