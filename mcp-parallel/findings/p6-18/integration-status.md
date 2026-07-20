# P6.18 integration attempt — iter16 report

## Attempted

1. Grep + live HTTP probe for `broker_send_rpc` and `/{org}/rpc` — **not landed**
2. Verified docker stack up (frontend :8180, control :8100, gateway :8300, broker :8311)
3. Browser MCP: logged in as `admin@zeroshield.io`, navigated to MCP Guardrails panel (`?tab=firewall-1-4`)
4. Cursor-owned test gates (read-only assessment)
5. Claude harness scripts assessed read-only (not executed — require wiring + 3-org setup)

## Live MCP inventory (zeroshield org only)

| Server | Transport | Auth | oauth_authorized |
|--------|-----------|------|------------------|
| Linear (manual OAuth) | streamable-http | oauth | true |
| Everything MCP | stdio | none | — |
| Linear MCP | stdio | none | false (pending) |
| linear-mcp-p1-repro | stdio | none | false (pending) |

**Gap vs P6 requirement:** 15 servers across 3 orgs not provisioned. Adversarial org slugs (`adv-org-alpha`, `adv-org-beta`, `adv-org-gamma`) exist only in test fixtures / gateway pytest, not in live control plane.

## Harness assessment (read-only)

| Script | Owner | Runnable now? | Notes |
|--------|-------|---------------|-------|
| `scripts/mcp_live_matrix_harness.py` | shared | Partial | Needs `HARNESS_TOKEN`; drives gateway tools/call — stdio-only path works; HTTP/SSE bypass sandbox |
| `gateway/.../test_mcp_sandbox_parallel_load.py` | Claude | Opt-in | `RUN_MCP_SANDBOX_DOCKER=1`; 5 orgs × 5 stdio — broker stdio path only |
| `gateway/.../test_mcp_sandbox_adversarial.py` | Claude | Opt-in | Cross-org leakage — requires adversarial org sandboxes |

## Transport routing (current gateway behavior)

| Transport | Path today | Target (contract) |
|-----------|--------------|-------------------|
| stdio | gateway → broker `/stdio/rpc` → agent `/rpc` | ✅ (stdio only) |
| websocket | gateway adapter → broker (partial) | broker unified `/rpc` |
| streamable-http | gateway **direct httpx** → upstream URL | broker → agent upstream proxy |
| sse | gateway **direct httpx** → upstream URL | broker → agent upstream proxy |

## P6.19 prep (P1 re-verify)

| Bug | Script | Result iter16 |
|-----|--------|---------------|
| B1 (oauth+stdio / 2 buttons) | `playwright_mcp_b1_b2_b4_e2e.mjs` | PASS (combined) |
| B2 (0-tools before oauth) | same | PASS |
| B4 (dialog focus) | same | PASS |
| B3 (sandbox unavailable) | not re-run destructive repro | prior fix (P5.19 warm) still in place |
| P4.13 (4 transports via sandbox) | — | **STILL BLOCKED** |

## User actions requested

1. Click **Authorize** on pending Linear stdio cards in the open browser (or use `scripts/ralph/headed_browser.sh` + noVNC :6080 for provider consent popups).
2. Provide **bearer tokens** if registering GitHub MCP or other PAT-gated HTTP servers.
3. Confirm when Claude gateway branch is merged so iter17 can re-probe integration.
