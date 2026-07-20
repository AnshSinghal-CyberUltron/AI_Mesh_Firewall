# P4.13 / P6.18 recheck — iter34 (2026-07-02)

## Claude lands recheck (read-only)

**No change since iter33.** Both Claude-owned ws blockers remain open.

### 1. Gateway ws routing — NOT LANDED

`mcp_proxy.py` still routes websocket via in-gateway `mcp_ws_adapter`, not `broker_send_rpc`:

| Transport | Gateway path | Lines |
|-----------|--------------|-------|
| streamable-http / sse | `broker_send_rpc` | ~1958–1990 |
| stdio | `mcp_stdio_adapter` → broker | ~1991–2004 |
| **websocket** | **`mcp_ws_adapter.send_jsonrpc`** | **~2005–2015** |

`_is_sandbox_routed` claims websocket always routes via sandbox (`:1931`), but `_adapter_forward` still dials upstream in-gateway for ws.

**Claude action:** Replace websocket branch with `broker_send_rpc` (mirror streamable-http/sse block).

### 2. Control URLField ws support — NOT LANDED

`MCPServerRegistration.url` is still `models.URLField` (`models.py:41-45`). Django rejects `ws://` at the model layer (`Enter a valid URL.`). Serializer SSRF guard already allows ws/wss (`serializers.py:154-158`) but registration cannot persist ws URLs.

**Claude action:** `CharField` + serializer validation, or custom URL validator accepting ws/wss.

### git log -3 (unchanged)

```
826d9908 ralph(mcp): P4.13/P6.18 — enable HTTP-via-sandbox by default …
bb4983da ralph(mcp): P4.13/P6.18 §3-proxy — route streamable-http/sse via broker_send_rpc …
3a7e06a3 ralph(mcp-backstop): CHG-0006 — per-key authz parity on bare REST route …
```

## 3-transport verify (1×, no regression)

`ROUNDS=1 TRANSPORT_MANIFEST=TRANSPORT_MANIFEST.zeroshield.json mcp_sandbox_transport_verify.py`

| Transport | Cold run | Warm retry | Notes |
|-----------|----------|------------|-------|
| stdio | PASS | PASS | 13 tools, echo ok |
| streamable-http | FAIL | PASS | *cold tools=0; recovered after warmup (same as iter33 R1) |
| sse | FAIL | PASS | *cold tools=0; recovered after warmup |
| websocket | BLOCKED | BLOCKED | No slug in manifest (URLField blocks registration) |

**Verdict:** 3/4 gateway transports green after warmup; **no regression** vs iter33. Report: `sandbox_transport_verify_report.json`.

## Network assertion (ss)

Gateway container: **no :443** egress to external MCP hosts.

## Playwright

`NODE_PATH=tests/e2e/node_modules BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_b1_b2_b4_e2e.mjs` → **B1/B2/B4 ALL PASS** (12/12).

## P4.13 / P6.18 checkbox

**Cannot mark `[x]`** — 3/4 gateway transports; websocket blocked on **two** Claude-owned seams (unchanged).

## Hive broadcast (Claude action — file:line refs)

To close 4/4:

1. **`gateway/ai_mesh_gateway/mcp_proxy.py:2005-2015`** — Replace `mcp_ws_adapter.send_jsonrpc` with `broker_send_rpc` (mirror `:1958-1990` streamable-http/sse block).
2. **`control/ai_mesh_control/mcp_connector/models.py:41-45`** — Allow `ws://` / `wss://` on server `url` (URLField → CharField + serializer validation).
3. Re-run: `mcp_transport_stubs_up.sh` → `mcp_register_transport_servers.py` → `ROUNDS=3 mcp_sandbox_transport_verify.py` → ss :443 → Playwright.

Cursor seam ready: ws stub live (`mcp_ws_everything_stub.mjs`), broker-direct ws PASS (iter33).
