# P4.13 / P6.18 recheck — iter32 (2026-07-02)

## Root causes fixed (Cursor-owned agent)

| Issue | Root cause | Fix |
|-------|------------|-----|
| streamable-http `No valid session ID` | Stale `Mcp-Session-Id` cached in agent after stub/container restart; recovery existed but needed `_invalidate_upstream_session` to also tear down SSE reader state | `upstream_manager.py`: `_invalidate_upstream_session` + retry on session-rejection errors (already present; verified live after sandbox image rebuild) |
| SSE ~90s hang | Legacy SSE returns **202 Accepted** on POST; JSON-RPC response arrives on the **persistent GET /sse** stream. Old `_post_sse` blocked on `client.post()` body until timeout | New `sse_manager.py`: background GET /sse reader + stream POST + wait on message events |

## Sandbox image

Rebuilt `ai-mesh/mcp-sandbox:latest` from `services/mcp-broker/sandbox-image/Dockerfile`; recreated `zeroshield-mcp-sandbox` (and org-a/org-b).

## Four-transport e2e (`ROUNDS=3`, `TRANSPORT_MANIFEST.zeroshield.json`)

| Transport | tools/list | tools/call | Notes |
|-----------|------------|------------|-------|
| stdio | PASS (13 tools) | PASS echo | via sandbox |
| streamable-http | PASS (12 tools) | PASS echo | ~95–340 ms |
| sse | PASS (12 tools) | PASS echo | ~1–2.5 s (SSE reader warm) |
| websocket | BLOCKED | BLOCKED | No `ws-everything` stub; gateway still uses `mcp_ws_adapter` (Claude-owned) |

**Verdict:** `SANDBOX_TRANSPORT: FAIL` (websocket slug missing only). Report: `sandbox_transport_verify_report.json`.

## Network assertion (ss)

During verify: gateway **no :443** egress to external MCP hosts.

## Playwright

`NODE_PATH=tests/e2e/node_modules BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_b1_b2_b4_e2e.mjs` → **B1/B2/B4 ALL PASS** (12/12).

## Broker gate

`services/mcp-broker/.venv/bin/python -m pytest sandbox-image/agent/tests` → **42 passed**; agent + sandbox routes/lifecycle → **83 passed**.

## P4.13 / P6.18 checkbox

**Cannot mark `[x]`** — 3/4 transports green; websocket still not through sandbox (`mcp_ws_adapter` + no ws stub). Claude-owned: route ws via `broker_send_rpc` or document legacy.

## Websocket (documented Claude-owned)

- `@modelcontextprotocol/server-everything` has no websocket mode → no trivial ws stub in `mcp_transport_stubs_up.sh`.
- Gateway hot path: `mcp_ws_adapter.py` (`websockets.client.connect`), not `broker_send_rpc`.
- Agent **does** implement websocket via `ws_manager.py` (unit-tested); seam is gateway wiring + stub registration.
