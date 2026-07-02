# BACKSTOP hardening — ext_mcp_proxy response-side memory-DoS closed (CHG-0064)

- **Item:** G3 item 10 ("resource limits CPU/mem/disk/timeout enforced + containment") /
  "resource bombs (mem) contained". Response-side twin of CHG-0063 (request-side body cap).
- **Change-id:** CHG-0064 (2026-07-02)
- **Type:** Memory-exhaustion DoS from an untrusted upstream (unbounded response buffering), fail-closed.

## Gap
The tenant-facing external MCP passthrough `ext_mcp_proxy` buffers the WHOLE response from an
UNTRUSTED external server before scanning/forwarding it:
- `sse_bytes = await resp.aread()` (finite SSE tools/call/resources/prompts result, ~line 1489)
- `body_bytes = await resp.aread()` (normal JSON / text / binary response, ~line 1560)
`resp.aread()` reads the entire body into memory with NO size ceiling. The code comment claimed it was
"capped by the httpx timeout" — but a timeout bounds TIME, not SIZE: a malicious / compromised external
MCP server (tenant-configured) can stream a multi-GB response FAST (within the timeout) and exhaust the
shared gateway's memory → OOM. Because the gateway is shared across tenants, this is a CROSS-TENANT DoS
one tenant's server can inflict on all others.

## Fix — `gateway/ai_mesh_gateway/mcp_proxy.py`
- New `_MCP_MAX_RESPONSE_BYTES` (env `MCP_MAX_RESPONSE_BYTES`, default 10MiB) + `_read_response_capped(resp)`:
  iterates `resp.aiter_bytes()` incrementally and raises `_MCPBodyTooLarge` (reused from CHG-0063) the
  instant the running total crosses the ceiling — so the gateway never holds more than the ceiling from
  an untrusted upstream in memory.
- Both `aread()` sites now use `_read_response_capped`; on `_MCPBodyTooLarge` the response is WITHHELD
  with a 502 `_mcp_upstream_too_large_response()` (`mcp_upstream_response_too_large`) after closing the
  upstream stream + client.
- The non-finite SSE passthrough (`async for chunk in resp.aiter_bytes(): yield chunk`) is UNCHANGED —
  it streams chunk-by-chunk and never holds the whole body in memory, so it was never the DoS.

## Scope
The untrusted-upstream boundary is the ext-proxy path (tenant-configured external servers). The ORG
path is sandbox-routed (`broker_send_rpc`) — the sandbox is OUR gVisor-contained infra with its own
mem/disk limits, so a huge sandbox response is contained by the sandbox, not the gateway. Env override
lets operators forward legitimately large tool results.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py ai_mesh_gateway/tests/test_mcp_body_cap.py -q`
  → 51 passed. New: an oversized JSON upstream response → 502 `mcp_upstream_response_too_large`; an
  oversized SSE upstream response → 502; a normal-sized response is unaffected; `_read_response_capped`
  raises over the ceiling / returns joined under it. The existing 39 ext-proxy tests keep passing (their
  response doubles were updated to expose `aiter_bytes()`, matching real httpx responses).
- Full sweep `ai_mesh_gateway/tests` → 1242 passed, 0 failed.
