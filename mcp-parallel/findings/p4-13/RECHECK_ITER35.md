# P4.13 / P6.18 recheck — iter35 (2026-07-02)

## Claude lands recheck (read-only)

**No change since iter34.** Both Claude-owned ws blockers remain open.

### 1. Gateway ws routing — NOT LANDED

`mcp_proxy.py` still routes websocket via in-gateway `mcp_ws_adapter`, not `broker_send_rpc`:

| Transport | Gateway path | Lines |
|-----------|--------------|-------|
| streamable-http / sse | `broker_send_rpc` | ~1958–1990 |
| stdio | `mcp_stdio_adapter` → broker | ~1991–2004 |
| **websocket** | **`mcp_ws_adapter.send_jsonrpc`** | **~2016–2026** |

### 2. Control URLField ws support — NOT LANDED

`MCPServerRegistration.url` is still `models.URLField` (`models.py:41-45`). Django rejects `ws://` at the model layer.

## Cursor-owned: cold-run tools=0 flake — FIXED

**Root cause:** SSE (and websocket) did not run the MCP `initialize` handshake on the first real method — only streamable-http did. After stub/sandbox recreate, a cold `tools/list` could return `tools=[]` (HTTP 200) while R2+ succeeded once the session warmed.

**Fix** (`upstream_manager.py`):

1. Auto-init on first real method for **streamable-http, sse, and websocket** (`_AUTO_INIT_TRANSPORTS`).
2. If `tools/list` succeeds with zero tools, invalidate the session, re-init, and **retry once** (covers stale sessions after upstream stub recreate).
3. Stale-session retry also matches connection errors (stub still booting).

**Tests:** +2 in `test_upstream_proxy.py` (`test_sse_cold_tools_list_auto_inits`, `test_streamable_http_empty_tools_list_retries`). Agent suite **12/12**; broker gate **85 passed**.

**Image:** Rebuilt `ai-mesh/mcp-sandbox:latest`; recreated org sandboxes.

## 3-transport verify (`ROUNDS=3`, cold sandbox + stubs)

`ROUNDS=3 TRANSPORT_MANIFEST=TRANSPORT_MANIFEST.zeroshield.json mcp_sandbox_transport_verify.py`

| Transport | R1 | R2 | R3 | Notes |
|-----------|----|----|-----|-------|
| stdio | PASS | PASS | PASS | tools=13 |
| streamable-http | PASS | PASS | PASS | tools=12 (cold R1 fixed) |
| sse | PASS | PASS | PASS | tools=13 |
| websocket | BLOCKED | BLOCKED | BLOCKED | No slug in manifest (URLField) |

**Verdict:** 3/3 gateway transports green **including R1**; websocket still blocked. Report: `sandbox_transport_verify_report.json`.

## Network assertion (ss)

Gateway container: **no :443** egress to external MCP hosts.

## Playwright

`NODE_PATH=tests/e2e/node_modules BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_b1_b2_b4_e2e.mjs` → **B1/B2/B4 ALL PASS** (12/12).

## Broker gate

`services/mcp-broker/.venv/bin/python -m pytest sandbox-image/agent/tests tests/test_sandbox_lifecycle.py tests/test_sandbox_routes.py -q` → **85 passed**.

## P4.13 / P6.18 checkbox

**Cannot mark `[x]`** — 3/4 gateway transports; websocket blocked on two Claude-owned seams (unchanged).
