# P6.19 — P1 repro re-run (iter17)

**Date:** 2026-07-02  
**Agent:** cursor-ralph-iter17  
**Story:** P6.19 prep — re-run P1 UI bug repros before COMPLETE

## Gateway blocker (unchanged)

| Probe | Result |
|-------|--------|
| `POST /v1/sandbox/zeroshield/rpc` | **404** — unified broker route still absent |
| `broker_send_rpc` in gateway | **MISSING** |
| HTTP/SSE direct upstream in `mcp_proxy.py` | **YES** — P4.13 / P6.18 remain blocked |

## P1 UI repro re-run (Playwright)

| Bug | Gate | Result |
|-----|------|--------|
| B1 — oauth+stdio / dup Authorize | `scripts/playwright_mcp_b1_b2_b4_e2e.mjs` (B1 asserts) | **PASS** — 1 Authorize, no "no URL" |
| B2 — 0-tools before oauth | same script (B2 asserts) | **PASS** — "Pending authorization" + "Authorize to load tools" |
| B4 — dialog focus loss | same script (B4 asserts) | **PASS** — focus kept all fields |
| B3 — sandbox unavailable | prior iter (broker stopped repro) | **unchanged** — fix in #19/#20/#21 |

**Combined E2E:** 12/12 PASS (`BASE_URL=http://127.0.0.1:8180`)

## P4 verification 3×

**NOT RUN** — blocked until Claude lands gateway+broker unified `/rpc` wiring per `docs/mcp/gateway-integration-checklist.md`.

## Cursor-side work this iter (P7.23 N1)

- Added `npm_config_ignore_scripts=true` to sandbox container env (`docker_manager.py`) — kills postinstall RCE during npx fetch.
- Broker lifecycle tests: 20/20 PASS; agent tests: 15/15 PASS.

## Next

1. Claude: land `broker_send_rpc` + `POST /{org}/rpc` + route all transports through sandbox.
2. Re-run P4.13 + P6.18 integration + network assertion.
3. P7.23 N2–N7: pinned packages, allowlist parity, registry pin.
