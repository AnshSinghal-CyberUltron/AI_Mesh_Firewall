# CHG-0140 — internal chat-pipeline MCP routes had no inbound body-size cap (unbounded-buffer DoS)

**Change-id:** CHG-0140
**Date:** 2026-07-03
**Severity:** MEDIUM (DoS — the two internal MCP routes read `await request.json()` with no size cap, so a large inbound body buffers unbounded into gateway memory; the chat user's tool ARGUMENTS flow through these routes, so the body is attacker-influenced).
**Area:** HARDEN THE ARCHITECTURE — gateway validation / DoS bounds; parity across MCP entry points (CHG-0034/0063).
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`internal_tools_call`, `internal_discover_tools`); `gateway/ai_mesh_gateway/tests/test_mcp_body_cap.py` (+4 tests).
**Whose work it touches:** the owning-session gateway internal (chat-pipeline) MCP routes.

## Root cause

Every MCP entry point is supposed to cap its inbound body: `_mcp_body_too_large` (Content-Length pre-check,
CHG-0034) + `_mcp_read_body_capped` (streaming byte-cap for chunked/no-Content-Length bodies, CHG-0063). The
`_mcp_body_too_large` docstring even states the streaming cap is used by "**every MCP entry point**". But two
routes had **omitted** it:

- `internal_tools_call` (`/v1/mcp/internal/tools-call`) — the chat-pipeline tool executor.
- `internal_discover_tools` (`/v1/mcp/internal/discover-tools`) — the chat-pipeline tools/list.

Both did `body = await request.json()` directly, which buffers the **entire** body into memory with no bound.
The other three routes (`org_mcp_jsonrpc`, `org_mcp_tool_call`, `ext_mcp_proxy`) all cap. The internal routes are
called by the trusted control-plane backend, but the payload carries the **chat user's tool arguments** (a user
can send an arbitrarily large tool-call argument via chat), so a huge body reaches the gateway and buffers
unbounded → memory-exhaustion DoS. The gateway must defend itself (defense-in-depth) rather than trust the
backend to have pre-capped.

## The fix (CHG-0140)

Both internal routes now apply the same cap as the other MCP routes, immediately after the internal-key check
and before `request.json()`:

```python
if _mcp_body_too_large(request):
    return _mcp_body_too_large_response()
try:
    await _mcp_read_body_capped(request)   # caches capped bytes into request._body
except _MCPBodyTooLarge:
    return _mcp_body_too_large_response()
```

`_mcp_read_body_capped` caches the capped bytes into `request._body`, so the subsequent `request.json()` reuses
them (no double read). An oversized body → HTTP 413 `mcp_body_too_large`.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_body_cap.py -q   # 13 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                        # 1930 passed, 0 failed
```

New tests (parametrized over BOTH internal routes): an oversized `Content-Length` → 413 (pre-check); a chunked
body with no Content-Length exceeding the (patched-small) ceiling → 413 (streaming cap). All 5 MCP routes now
apply `_mcp_read_body_capped` (grep-verified).

## Scope / honesty note

Closes the last MCP entry points missing the body cap; brings them to parity with the org/ext routes. Behavior
for legitimate (under-ceiling) bodies is unchanged (they still parse). Does not change the host-blocked
live-stress status (items 14–19). Partial coverage is not completion.
