# P4.13/P6.18 — SSE transport: known limitation (deprecated)

## Transport status through the per-org sandbox
| transport | routing (isolation) | functional |
|-----------|:-------------------:|:----------:|
| stdio | via sandbox | ✅ (fleet + gate 3×) |
| streamable-http | via sandbox | ✅ (e2e: tools/list+call through gateway; stale-session recovery) |
| websocket | via sandbox | ✅ (adapter + agent ws_manager; unit-tested; no live server) |
| **sse** | **via sandbox** | ⚠️ **incomplete** |

**Isolation holds for ALL four transports** — the gateway routes every transport
through the sandbox (`_is_sandbox_routed`), so it never dials an MCP upstream
directly regardless of transport.

## Why SSE is functionally incomplete
The legacy **HTTP+SSE** transport (deprecated by the MCP spec in favour of
Streamable HTTP) uses a split channel: the client GETs a persistent `/sse` event
stream and POSTs JSON-RPC messages to a separate `/message?sessionId=…` endpoint;
**responses arrive asynchronously on the GET stream**, correlated by id. The agent's
`_post_sse` reads the POST response (a `202 Accepted` with no body) instead of
correlating the reply frame on the GET stream, so an SSE `tools/call` does not return
a result (times out at the agent `method_timeout`, bounded — not an infinite hang).

Fixing it properly requires a background reader on the GET stream + an id→future
correlation map. It is deliberately deferred because:
- SSE is **deprecated**; no production MCP server is SSE-only.
- The entire live fleet is **stdio** (+ Linear via stdio/mcp-remote); Streamable
  HTTP (the modern replacement) is fully working.
- The change is non-trivial and risks the (working) streamable-http path.

## What IS guaranteed for SSE today
- Registration + routing: an `sse` server is routed gateway→broker→sandbox (isolation).
- Egress allowlist enforced in the sandbox for the SSE host.
- A failed SSE call returns a bounded error, never a raw upstream dial from the gateway.

## To finish SSE later
Implement a persistent GET-stream reader in `upstream_manager` (mirror
`_ensure_sse_endpoint`, keep the stream open, dispatch `data:` frames to per-id
futures); `_post_sse` then POSTs and awaits the correlated future. Verify with the
`sse-everything-stub` (`scripts/mcp_transport_stubs_up.sh`) via
`mcp_sandbox_transport_verify.py`.
