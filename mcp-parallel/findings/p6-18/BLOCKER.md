# P6.18 — BLOCKED (gateway+broker wiring not landed)

**Iteration:** cursor-ralph-iter16  
**Date:** 2026-07-02  
**Story:** P6.18 — Integrate Claude gateway+broker changes; 4 transports via sandbox under 15-MCP harness

## Verdict

**BLOCKED** — Cannot mark P6.18 `[x]` without integration evidence. Claude-owned gateway wiring is **unchanged** since P4.13 blocker (iter13). No Claude branch merge detected on `main`.

## Gateway wiring status (Claude-owned)

Re-checked against `docs/mcp/gateway-integration-checklist.md`:

| Checklist item | Expected | Actual (iter16) |
|----------------|----------|-----------------|
| `broker_send_rpc` in `mcp_sandbox_client.py` | Unified RPC → `…/sandbox/{org}/rpc` | **MISSING** — still `broker_send_jsonrpc` → `…/stdio/rpc` (`:136-166`) |
| `POST /{org_slug}/rpc` in broker `routes.py` | Unified broker route | **MISSING** — HTTP **404** on probe; only `POST /{org_slug}/stdio/rpc` exists (`:212`) |
| `_sandbox_forward` / all transports in `mcp_proxy.py` | HTTP/SSE/WS → broker | **MISSING** — `is_adapter_transport = transport in ("stdio", "websocket")` (`:1899`); streamable-http/sse still **direct httpx** to upstream (`internal_discover_tools :1196-1232`) |
| 15-MCP harness (3 orgs × 5) | Live integration | **NOT RUNNABLE** — harness requires unified wiring; only **1 org** (`zeroshield`) with **4** servers in live stack |

### Live probes (2026-07-02)

```
POST http://127.0.0.1:8311/v1/sandbox/zeroshield/rpc     → 404 (route absent)
POST http://127.0.0.1:8311/v1/sandbox/zeroshield/stdio/rpc → 401 (route exists; auth required)
```

### Evidence (grep / file:line)

```
gateway/ai_mesh_gateway/mcp_sandbox_client.py:161  url = f"{_BROKER_URL}/v1/sandbox/{org_slug}/stdio/rpc"
gateway/ai_mesh_gateway/mcp_proxy.py:1196         # For streamable-http / sse: call the upstream MCP server directly
gateway/ai_mesh_gateway/mcp_proxy.py:1899         is_adapter_transport = transport in ("stdio", "websocket")
services/mcp-broker/src/sandbox/routes.py:212     @router.post("/{org_slug}/stdio/rpc")
```

## Cursor-owned seam status (ready — no mismatch found)

| Layer | Result | Command |
|-------|--------|---------|
| Agent unified `/rpc` | ✅ 15/15 | `cd services/mcp-broker && .venv/bin/python -m pytest sandbox-image/agent/tests -q` |
| Broker lifecycle / hardening | ✅ 20/20 | `.venv/bin/python -m pytest tests/test_sandbox_lifecycle.py -q` |
| Frontend B1/B2/B4 E2E | ✅ 12/12 | `NODE_PATH=tests/e2e/node_modules BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_b1_b2_b4_e2e.mjs` |
| Seam contract vs agent | ✅ aligned | `docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md` matches agent `main.py` |

**No Cursor-owned code changes required** this iteration — blocker is entirely on Claude-owned gateway/broker/harness paths.

## Docker stack (live)

| Service | Port | Status |
|---------|------|--------|
| frontend | 8180 | 200 |
| control | 8100 | healthy |
| gateway | 8300 | 200 |
| mcp-broker | 8311 | 200 |

## What needs the user

1. **Claude session** must land checklist §1–3 (`broker_send_rpc`, unified broker route, route all transports through sandbox).
2. **OAuth (manual):** Browser opened at `http://127.0.0.1:8180/?tab=firewall-1-4`. Servers awaiting auth:
   - `Linear MCP` (stdio) — **Authorize** button visible
   - `linear-mcp-p1-repro` (stdio) — **Authorize** button visible
   - `Linear (manual OAuth)` (HTTP) — already `oauth_authorized=true`
3. **15-MCP matrix:** Requires **3 orgs × 5 servers** — live stack has only `zeroshield` (4 servers). Need org provisioning + bearer tokens / gateway keys per org before harness.
4. **Bearer tokens:** GitHub MCP preset uses bearer auth — provide PAT when registering if testing HTTP bearer path.

## Unblock criteria

1. Claude merges gateway+broker wiring per `docs/mcp/gateway-integration-checklist.md`
2. `POST /v1/sandbox/{org}/rpc` returns non-404
3. Gateway `mcp_proxy` routes streamable-http/sse/ws through `broker_send_rpc` (no direct upstream httpx)
4. Re-run P4.13 + P6.18 harness with network assertion (gateway must not dial external MCP :443)

## Next steps

- **P6.18:** Retry after Claude wiring lands
- **P6.19 (prep done):** P1 B1/B2/B4 still green; P4.13 remains blocked (see `mcp-parallel/findings/p4-13/BLOCKER.md`)
