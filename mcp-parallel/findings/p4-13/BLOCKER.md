# P4.13 — BLOCKED (gateway wiring not landed)

**Iteration:** cursor-ralph-iter13  
**Date:** 2026-07-02  
**Story:** P4.13 — Verify all 4 transports through sandbox; no direct gateway upstream

## Verdict

**BLOCKED** — Cannot mark P4.13 `[x]` without evidence. Pivoted to P5.14 (frontend B1).

## Gateway wiring status (Claude-owned)

Checked against `docs/mcp/gateway-integration-checklist.md`:

| Checklist item | Expected | Actual (2026-07-02) |
|----------------|----------|---------------------|
| `broker_send_rpc` in `mcp_sandbox_client.py` | Unified RPC to `…/sandbox/{org}/rpc` | **MISSING** — still `broker_send_jsonrpc` → `…/stdio/rpc` only (`:136-166`) |
| `SandboxRpcRequest` + `POST /{org}/rpc` in broker `routes.py` | Unified broker route | **MISSING** — only `POST /{org}/stdio/rpc` (`routes.py:212`) |
| `_sandbox_forward` / `ALL_SANDBOX_TRANSPORTS` in `mcp_proxy.py` | All transports → broker | **MISSING** — `is_adapter_transport = transport in ("stdio", "websocket")` (`:1899`); streamable-http/sse still direct httpx to `server.url` (`internal_discover_tools :1196-1262`) |
| Network assertion (no gateway→external MCP) | Harness tcpdump | **NOT RUNNABLE** — gateway still dials upstream directly for HTTP/SSE |

### Evidence (grep)

```
gateway/ai_mesh_gateway/mcp_sandbox_client.py:161  url = f"{_BROKER_URL}/v1/sandbox/{org_slug}/stdio/rpc"
gateway/ai_mesh_gateway/mcp_proxy.py:1899         is_adapter_transport = transport in ("stdio", "websocket")
services/mcp-broker/src/sandbox/routes.py:212     @router.post("/{org_slug}/stdio/rpc")
```

### Cursor-side (ready)

- Agent unified `POST /rpc` — ✅ `sandbox-image/agent/main.py`
- Agent tests — ✅ `test_rpc_unified.py`, `test_upstream_proxy.py`
- gVisor hardening — ✅ P4.12 (`docker_manager.py`)

## What can be verified from Cursor-owned side today

| Layer | Verifiable now | Blocked until gateway wiring |
|-------|----------------|------------------------------|
| Agent unit tests | `pytest sandbox-image/agent/tests -q` | — |
| Broker lifecycle | `pytest services/mcp-broker/tests/test_sandbox_lifecycle.py` | Unified `/rpc` forward |
| docker_manager egress lockdown | lifecycle tests | — |
| End-to-end 4 transports via gateway | **NO** | `broker_send_rpc` + route all transports |
| Network assertion (no gateway→upstream) | **NO** | Claude harness + wiring |

## Unblock criteria (P6.18)

1. Claude lands checklist §1–3 (broker unified route, `broker_send_rpc`, `_sandbox_forward`)
2. Re-run P4.13 with Playwright + harness network capture
3. ALLOW: gateway→broker:8311, broker→agent:9320; DENY: gateway→external MCP :443

## Next iteration

- **P4.13:** Retry after Claude gateway branch merges
- **P5.14 (this iter fallback):** Frontend B1 verification + any safe frontend-only hardening
