# BACKSTOP hardening — chunked/no-Content-Length body memory-DoS closed (CHG-0063)

- **Item:** G3 item 9 (gateway validation/DoS) + "resource bombs (mem) contained" / CPU-mem limits.
  Closes CHG-0034's explicitly documented limitation.
- **Change-id:** CHG-0063 (2026-07-02)
- **Type:** Memory-exhaustion DoS (unbounded body buffering), fail-closed 413.

## Gap
`_mcp_body_too_large` (CHG-0034) rejects an oversized body BEFORE buffering — but it only inspects
the declared **Content-Length header**. A request that uses `Transfer-Encoding: chunked` (or simply
omits Content-Length) slips past the pre-check (it returns False), and the entry point then calls
`await request.body()` / `await request.json()`, which buffers the ENTIRE stream into memory with NO
ceiling. An attacker streams gigabytes in a chunked body → gateway memory exhaustion / OOM. The
`_mcp_body_too_large` docstring itself flagged this ("a chunked request that omits Content-Length is
not caught here"). It affects the three tenant-facing MCP entry points: `ext_mcp_proxy`,
`org_mcp_jsonrpc`, `org_mcp_tool_call` — exactly the three CHG-0034 added the (header-only) guard to.

## Fix — `gateway/ai_mesh_gateway/mcp_proxy.py`
- New `_MCPBodyTooLarge` + `async _mcp_read_body_capped(request)`: reads `request.stream()`
  incrementally, accumulating bytes, and raises `_MCPBodyTooLarge` the INSTANT the running total
  crosses `_MCP_MAX_BODY_BYTES` — so the gateway never holds more than the ceiling in memory,
  regardless of framing. The bytes are cached on `request._body` so a downstream `request.json()` /
  `request.body()` reuses the already-capped buffer (Starlette's own cache slot). A test-double
  fallback (no `stream()`) uses `body()` if present (still ceiling-checked), else no-ops.
- Wired at all three entry points: `ext_mcp_proxy` + `org_mcp_tool_call` read via
  `_mcp_read_body_capped` (returns bytes); `org_mcp_jsonrpc` calls it to cap+cache, then keeps
  `await request.json()` (which reuses the capped `request._body`). Each returns the existing 413
  `_mcp_body_too_large_response()` on `_MCPBodyTooLarge`.
- The cheap Content-Length pre-check is retained (fast reject for an honestly-declared oversize); the
  streaming cap is the backstop for the chunked/undeclared case.

## Scope
The three tenant-facing entry points (untrusted tenant surface). The backend-internal paths
(`internal_tools_call` / discovery, X-Gateway-Internal-Key trust) still use plain `request.body()` —
lower risk (internal auth boundary), a possible future follow-up for defence-in-depth.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_body_cap.py -q` → 7 passed:
  chunked body over ceiling raises; the cap STOPS reading early (memory bound — asserts <=2 of 4 chunks
  consumed); under-ceiling returns + caches (2nd call reuses cache); exactly-at-ceiling ok / +1 raises;
  a pre-buffered oversize `_body` still rejected; test-double body() fallback still capped; and an
  END-TO-END `org_mcp_tool_call` with an oversized chunked `stream()` → HTTP 413 `mcp_body_too_large`.
- Full sweep `ai_mesh_gateway/tests` → 1237 passed, 0 failed (no regression; the org_mcp_jsonrpc path
  keeps `request.json()` so its `.json()`-mocking test doubles are unaffected).
