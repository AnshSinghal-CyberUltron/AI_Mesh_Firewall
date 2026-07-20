# P4.13 / P6.18 recheck — iter33 (2026-07-02)

## Gateway ws routing recheck (read-only)

**No change since iter32.** `mcp_proxy.py` still routes websocket via in-gateway `mcp_ws_adapter`, not `broker_send_rpc`:

| Transport | Gateway path | Lines |
|-----------|--------------|-------|
| streamable-http / sse | `broker_send_rpc` | ~1958–1978 |
| stdio | `mcp_stdio_adapter` → broker | ~1979–1999 |
| **websocket** | **`mcp_ws_adapter.send_jsonrpc`** | **~2000–2010** |

`_is_sandbox_routed` claims websocket always routes via sandbox (`:1926`), but `_adapter_forward` still dials upstream in-gateway for ws. **Claude-owned:** switch websocket branch to `broker_send_rpc` (same upstream block as http/sse).

## Cursor-owned ws prep (DONE)

| Item | Status |
|------|--------|
| `scripts/mcp_ws_everything_stub.mjs` | NEW — minimal MCP-over-WebSocket echo stub |
| `scripts/mcp_transport_stubs_up.sh` | `upsert_ws` → `ws-everything` on `mcp_sandbox_net_zeroshield:3003` |
| Broker-direct ws RPC | **PASS** — tools/list (1 echo tool, 17 ms) + tools/call echo `iter33-ws-broker` |
| Control registration `ws://…` | **FAIL** — Django `URLField` rejects `ws://ws-everything.stub:3003/mcp` (`Enter a valid URL.`). Serializer SSRF guard allows ws/wss (`serializers.py:154`) but model field does not. **Claude-owned:** `CharField` or custom validator on `MCPServerRegistration.url`. |

## Four-transport e2e (`ROUNDS=3`, `TRANSPORT_MANIFEST.zeroshield.json`)

| Transport | R1 | R2 | R3 | Notes |
|-----------|----|----|-----|-------|
| stdio | PASS | PASS | PASS | 13 tools, echo ok |
| streamable-http | FAIL* | PASS | PASS | *R1 cold session (tools=0); recovered R2–3 |
| sse | PASS | PASS | PASS | ~43–230 ms |
| websocket | BLOCKED | BLOCKED | BLOCKED | No slug in manifest (registration blocked) |

**Verdict:** `SANDBOX_TRANSPORT: FAIL` (websocket only). Report: `sandbox_transport_verify_report.json`.

## Network assertion (ss)

Gateway container: **no :443** egress to external MCP hosts during verify.

## Playwright

`NODE_PATH=tests/e2e/node_modules BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_b1_b2_b4_e2e.mjs` → **B1/B2/B4 ALL PASS** (12/12).

## Broker gate

`services/mcp-broker/.venv/bin/python -m pytest sandbox-image/agent/tests tests/test_sandbox_lifecycle.py tests/test_sandbox_routes.py -q` → **83 passed**.

## P4.13 / P6.18 checkbox

**Cannot mark `[x]`** — 3/4 gateway transports green; websocket blocked on **two** Claude-owned seams:

1. Gateway: `mcp_ws_adapter` not migrated to `broker_send_rpc`.
2. Control: `URLField` cannot persist `ws://` URLs for registration/sync.

## Hive broadcast (Claude action)

To close 4/4:

1. **`mcp_proxy.py`:** Replace websocket branch in `_adapter_forward` with `broker_send_rpc` (mirror streamable-http/sse block; inject oauth token + upstream allowlist).
2. **`control/.../models.py`:** Allow `ws://` / `wss://` on server `url` (or use `CharField` + serializer validation).
3. Re-run: stubs up → `mcp_register_transport_servers.py` → `ROUNDS=3 mcp_sandbox_transport_verify.py` → ss :443 → Playwright.

Cursor seam is ready: ws stub live, agent `ws_manager.py` broker-direct verified.
