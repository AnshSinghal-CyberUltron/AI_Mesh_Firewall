# P4.13/P6.18 — 4-transport isolation COMPLETE + ACTIVE + validated

## §3-proxy (gateway) — DONE (commit bb4983da, default-on 826d9908)
`mcp_proxy.py`: `_is_sandbox_routed(transport)` (stdio/ws always; streamable-http/sse
via `MCP_HTTP_VIA_SANDBOX`, now DEFAULT ON) + `_adapter_forward` streamable-http/sse
branch → `mcp_sandbox_client.broker_send_rpc` (builds upstream url + egress allowlist
+ injected Bearer). 3 transport-decision sites unified. Verifier: **wiring landed: YES**
(broker_send_rpc refs=2, mcp_proxy_direct_httpx=NO). Unit 4/4; 105/105 gateway MCP
suite; 5 direct-path scan tests pinned `MCP_HTTP_VIA_SANDBOX=0` (transport-agnostic
scan covered there + by sandbox-routed tests).

## Agent streamable-http hang — FIXED (commit 23d0ba26)
`upstream_manager._post_streamable_http` rewritten to `client.stream("POST",…)` +
`aiter_lines()`, returning on the first matching-id SSE `data:` frame (was a
non-streaming `.post()` that hung on the persistent SSE stream). Added
`_initialize_session` auto-handshake (sandbox receives single methods; the MCP
initialize must run agent-side). Agent tests 41/41 (streamable-http tests updated to
fake streaming; websockets dep added).

## End-to-end validation (real streamableHttp Everything server)
- agent-in-isolation `/rpc`: tools/list **12 tools in 0.2s**, echo works (was 60s hang).
- broker→sandbox→upstream via a REAL org-a sandbox (new image): `tools/call echo` →
  `"Echo: broker-sandbox-http-FIXED"`, **status 200, 0.067s**, `upstream_status=200`,
  session established, per-org egress allowlist enforced.
- gateway→broker: `_adapter_forward` → `broker_send_rpc` unit-proven (no direct dial).
- Regression with flag ON: stdio concurrency PASS, **B1 8/8 PASS**, **P9 GATE PASS**.

## Status: ALL FOUR TRANSPORTS route through the per-org sandbox
stdio + mcp-remote + websocket + streamable-http + sse → gateway never dials an MCP
upstream directly; the sandbox agent dials (egress-allowlisted). §4 egress assertion
PASS for stdio (the live fleet); HTTP path proven functional end-to-end.
Rollback: `MCP_HTTP_VIA_SANDBOX=0` reverts HTTP to the legacy direct path.
