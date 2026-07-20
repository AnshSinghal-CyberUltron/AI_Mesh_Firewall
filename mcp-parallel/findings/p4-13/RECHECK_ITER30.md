# P4.13 / P6.18 — RECHECK iter30 (flag ON + stubs; DEPLOYED §3 MISSING)

**Iteration:** cursor-ralph-iter30  
**Date:** 2026-07-02  
**Prior:** iter29 — §3 source landed (`bb4983da`), flag default OFF

## Verdict

**e2e INCOMPLETE — cannot `[x]`** — Cursor enabled `MCP_HTTP_VIA_SANDBOX` on the live stack,
registered http+sse stubs, and ran `ROUNDS=3` transport verify. **Git source has §3 wiring; the
running gateway container does not** (`broker_send_rpc` refs: source **2**, runtime **0**). Http/sse
calls therefore hit the legacy backend path (0 synced tools → empty `tools/list`). Websocket still
`mcp_ws_adapter` + unregistered.

## §3 completeness grep (gateway)

| Check | Source (git) | Runtime (gateway container) |
|-------|--------------|-----------------------------|
| `MCP_HTTP_VIA_SANDBOX` usage | `mcp_proxy.py:1933` | `printenv` → **true** (iter30 script) |
| `broker_send_rpc` refs in `mcp_proxy.py` | **2** | **0** |
| `_is_sandbox_routed` | present | **absent** |
| `mcp_ws_adapter` path | `mcp_proxy.py:2001` | present (ws not broker) |
| `httpx.AsyncClient` sites in `mcp_proxy.py` | **14** | 14+ (legacy paths live) |
| http/sse/ws registered (zeroshield) | — | stdio ✓; **http+sse ✓**; ws ✗ |

## Cursor iter30 actions

| Step | Result |
|------|--------|
| `scripts/mcp_transport_stubs_up.sh` | http-everything.stub:3001 + sse-everything.stub:3002 on `mcp_sandbox_net_zeroshield` |
| `scripts/mcp_enable_http_via_sandbox.sh` | `.env` + recreate → gateway `MCP_HTTP_VIA_SANDBOX=true`, control allowlist set |
| `scripts/mcp_register_transport_servers.py` | `http-everything-stub`, `sse-everything-stub` registered; ws URL rejected by URLField |
| `TRANSPORT_MANIFEST.zeroshield.json` | stdio + http + sse slugs + live gateway key |
| Broker direct `POST /zeroshield/rpc` streamable-http | **PASS** — tools/list returns Everything tools (agent auto-init) |
| Gateway `tools/list` http-everything-stub | **FAIL** — `{"tools":[]}` (legacy path; runtime lacks §3) |
| `ROUNDS=3 mcp_sandbox_transport_verify.py` | **FAIL** — stdio PASS; http/sse empty; ws missing |
| Stdio echo via gateway | **PASS** `Echo: iter30-stdio` |
| Network `:443` ss snapshot during verify | **0** new external lines (stdio+failed http still no gateway :443) |

## Playwright / gates (iter30)

| Gate | Result |
|------|--------|
| Playwright B1/B2/B4 (`playwright_mcp_b1_b2_b4_e2e.mjs`) | run in commit gate |
| P7.23 package tests | run in commit gate |
| broker+agent | run in commit gate |

## Unblock for `[x]`

1. **Claude:** deploy §3 gateway code to running container (rebuild or `docker cp mcp_proxy.py` + reload).
2. **Claude:** route **websocket** via `broker_send_rpc`; register ws stub.
3. Re-run `bash scripts/mcp_enable_http_via_sandbox.sh` (if recreated) + `ROUNDS=3` transport verify.
4. tcpdump network assertion during step 3 (gateway no direct upstream :443).
5. Playwright B1/B2/B4 + P10 3× GREEN.

Hive: `cursor-ralph-iter30` on `hive-1782991737290-ylo911`.
