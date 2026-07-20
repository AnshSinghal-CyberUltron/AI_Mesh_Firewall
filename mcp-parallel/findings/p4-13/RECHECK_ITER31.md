# P4.13 / P6.18 recheck — iter31 (2026-07-02)

## Deploy §3 (no gateway source edits)

| Check | iter30 | iter31 |
|-------|--------|--------|
| Git `mcp_proxy.py` `broker_send_rpc` refs | 2 | 2 |
| **Running** `ai_mesh_firewall-gateway-1` refs | **0** (stale image) | **2** |
| `MCP_HTTP_VIA_SANDBOX` on gateway | `true` | `true` |

Actions: `docker compose build gateway` + `docker compose up -d --force-recreate gateway`.

## Wiring gate

`scripts/mcp_sandbox_transport_verify.py` wiring section: **LANDED** (`broker_send_rpc` YES, unified broker route YES, `mcp_proxy_direct_httpx` NO).

## Four-transport e2e (`ROUNDS=3`, `TRANSPORT_MANIFEST.zeroshield.json`)

| Transport | tools/list | tools/call | Notes |
|-----------|------------|------------|-------|
| stdio | PASS (13 tools) | PASS echo | via sandbox |
| streamable-http | FAIL (tools=0) | FAIL | JSON-RPC error: upstream HTTP 400 — *No valid session ID provided* (streamable-http session handshake) |
| sse | FAIL (timeout ~90s) | FAIL | http=0 / hung |
| websocket | BLOCKED | BLOCKED | No `ws-everything` stub; manifest has no ws slug; gateway still uses `mcp_ws_adapter` hot path (0 `broker_send_rpc` in `mcp_ws_adapter.py`) |

**Verdict:** `SANDBOX_TRANSPORT: FAIL` — report `sandbox_transport_verify_report.json`.

## Network assertion (ss)

During verify window: `docker exec gateway ss -tnp | grep ':443'` → **no gateway :443** (no direct upstream MCP TLS).

## Stubs

`scripts/mcp_transport_stubs_up.sh` — http-everything + sse-everything on `mcp_sandbox_net_zeroshield`; ws stub absent (documented).

## Playwright

`NODE_PATH=tests/e2e/node_modules BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_b1_b2_b4_e2e.mjs` → **B1/B2/B4 ALL PASS** (12/12).

## P4.13 / P6.18 checkbox

**Cannot mark `[x]`** — not all four transports e2e green; ws not through sandbox; http/sse session path still failing at gateway after §3 deploy.

## Next (Claude-owned unless Cursor seam)

1. Fix streamable-http session init through broker→sandbox→stub (or extend verify harness with initialize).
2. Debug sse sandbox upstream path (90s hang).
3. Route websocket via `broker_send_rpc` + ws stub, or hive consensus that ws is a separate story item.
