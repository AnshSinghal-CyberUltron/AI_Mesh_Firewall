# P4.13 / P6.18 — RECHECK iter29 (§3 WIRING LANDED — e2e INCOMPLETE)

**Iteration:** cursor-ralph-iter29  
**Date:** 2026-07-02  
**Prior:** iter28 BLOCKED (0 `broker_send_rpc` refs in `mcp_proxy.py`)

## Verdict

**WIRING LANDED, e2e INCOMPLETE** — Claude commit `bb4983da` landed §3-proxy code:
`_is_sandbox_routed` + `_adapter_forward` now call `broker_send_rpc` for streamable-http/sse when
`MCP_HTTP_VIA_SANDBOX=1`. Wiring gate reports **YES** (2 `broker_send_rpc` refs; `mcp_proxy_direct_httpx`
false). **Cannot mark P4.13/P6.18/P6.19 `[x]`** — runtime flag defaults **OFF**, websocket still uses
in-gateway `mcp_ws_adapter` (not broker), http/sse/ws servers not registered, 14 `httpx.AsyncClient` sites
remain on legacy paths.

## vs iter28

| Check | iter28 | iter29 |
|-------|--------|--------|
| `mcp_proxy` `broker_send_rpc` refs | **0** | **2** (`:1958`, `:1974`) |
| Wiring gate `landed` | NO | **YES** |
| `MCP_HTTP_VIA_SANDBOX` in gateway | n/a | **unset** (default off) |
| http/sse/ws registered in zeroshield | no | **no** |
| Stdio e2e via gateway | GREEN | **GREEN** |
| 4-transport `mcp_sandbox_transport_verify` | BLOCKED | **FAIL** (stdio ok; 3 transports missing) |

## Live probes

| Probe | HTTP | Notes |
|-------|------|-------|
| `POST :8311/v1/sandbox/zeroshield/rpc` | 401 | unified route live |
| `POST :8311/v1/sandbox/zeroshield/stdio/rpc` | 401 | stdio route live |
| `GET :8311/health` | 200 | broker up |
| Stdio echo `everything-1` | 200 | `Echo: canary-iter29` via gateway key from scale manifest |

## Code (post-`bb4983da`)

```bash
grep -c broker_send_rpc gateway/ai_mesh_gateway/mcp_proxy.py   # → 2
grep -c httpx.AsyncClient gateway/ai_mesh_gateway/mcp_proxy.py # → 14
docker exec $(docker ps -qf name=gateway) printenv MCP_HTTP_VIA_SANDBOX  # → empty (off)
```

- `test_mcp_http_via_sandbox.py` added (flag gating + broker path unit tests).
- Websocket: `_is_sandbox_routed("websocket")` true but `_adapter_forward` still calls `mcp_ws_adapter`
  directly — broker unified path not wired for ws yet.

## Cursor gates (iter29)

| Gate | Result |
|------|--------|
| Wiring gate (`mcp_sandbox_transport_verify.py`) | **LANDED** (exit proceeds; transport round FAIL) |
| Stdio transport (scale manifest key) | **PASS** tools/list 13 tools + echo |
| `mcp_multi_org_harness.py` 1× | **GREEN** (15-MCP fleet) |
| Playwright B1/B2/B4 | **12/12 PASS** |
| Gateway :443 egress (`ss` snapshot) | **0** external :443 lines during stdio probe |
| P4.13/P6.18 `[x]` | **NO** — need flag on + 4 transports registered + ROUNDS=3 all-green |

## Unblock for `[x]` (Claude + Cursor)

1. Set `MCP_HTTP_VIA_SANDBOX=1` on gateway (compose/env).
2. Route **websocket** through `broker_send_rpc` (parity with http/sse).
3. Register http/sse/ws servers per `TRANSPORT_REGISTRATION.md`; fill manifest (key from
   `scripts/ralph/.mcp_scale_manifest.json`, not committed).
4. `TRANSPORT_MANIFEST=... ROUNDS=3 python3 scripts/mcp_sandbox_transport_verify.py` → PASS.
5. tcpdump network assertion (script guide) during step 4.
6. `ROUNDS_GATE=3 python3 scripts/mcp_p10_recursive_gate.py` (includes Playwright).

Hive: `cursor-ralph-iter29` joined `hive-1782991737290-ylo911`.
