# BACKSTOP audit — MCP Redis write surface (post-CHG-0048) + OAuth token TTL regression guard (CHG-0049)

- **Item:** G3 item 11 ("PostgreSQL + Redis schemas/usage/restart-safety").
- **Change-id:** CHG-0049 (2026-07-02)
- **Type:** Verification (audit) + regression guard. No production code change — the
  audit confirmed the surface is correct after CHG-0048; the new tests pin the one
  correctness property that had no test.

## Motivation
CHG-0048 fixed a non-atomic INCR+EXPIRE TTL race on the tool-call cap counter. This
audit sweeps EVERY Redis write in the MCP surface for the same class of bug
(TTL never set / not atomic / not self-healing) and for token-lifetime correctness.

## Every MCP Redis write, audited
1. `mcp_oauth_proxy._flow_save` → `rc.setex(mcp:oauth:flow:{state}, _FLOW_TTL=600, ...)`
   — ATOMIC (SET+EXPIRE in one command). Flow state is `rc.delete(...)`d on pop
   (`_flow_pop`), so used-once CSRF/PKCE state cannot be replayed. **CLEAN.**
2. `mcp_oauth_proxy._token_save` → `rc.setex(mcp:oauth:token:{org}|{url}, ttl, ...)`
   — ATOMIC, and the `ttl` is DERIVED from the access token's own `expires_at`
   (`max(int(expires_at - now) + 60, 300)`), floored to 300s, and bumped to
   `_TOKEN_DEFAULT_TTL` when a `refresh_token` is present (so the refresh token
   outlives the access token to allow rotation). **CLEAN.** The consumer
   `get_stored_token` re-checks `expires_at` and refreshes at `expires_at - 30`,
   and the `+60` TTL grace keeps the key present across that refresh window.
3. `mcp_proxy._incr_tool_call_count` → atomic `pipeline(INCR, EXPIRE NX)` — fixed by
   CHG-0048. **CLEAN.**
4. `mcp:scan_ver:*` — the gateway only READS it (`mget` in `_current_scan_version`);
   it is written by the control plane on policy change. No gateway TTL concern.
   **CLEAN (read-only on the gateway).**

Result: the MCP Redis write surface is correct after CHG-0048. No new race found.

## Notes (documented non-issues)
- The in-process `_oauth_tokens` dict is a deliberate fast-path / Redis-down fallback.
  `_token_load` returning a record even when Redis has expired it is BY DESIGN:
  `get_stored_token` re-checks `expires_at` (refresh-or-None), and `has_stored_token`
  intentionally reports existence (incl. expired → "needs re-auth") per its docstring.
  Its size is bounded by config cardinality (distinct org×server that authenticated),
  not request volume — not a request-driven unbounded leak.

## Regression guard added
`_token_save`'s expiry-derived TTL had NO test — a refactor to a fixed TTL would
silently serve expired access tokens or evict valid ones early. New
`gateway/ai_mesh_gateway/tests/test_mcp_oauth_token_ttl.py` (+4) pins it: TTL derived
from `expires_at` (+60 grace, not the default); a short token floored to 300s; the
30-day default when no `expires_at`; and a refresh_token keeping the key alive ≥ the
default TTL.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_oauth_token_ttl.py -q`
  → **4 passed** (uses a recording Redis stub to assert the exact `setex` TTL).
- Broad sweep `ai_mesh_gateway/tests` → **1109 passed, 0 failed**.

## Residual
Item 11's live kill-Redis-mid-load drill (recovery + no leakage during recovery)
remains host-blocked (item 18 chaos, needs a dedicated host).
