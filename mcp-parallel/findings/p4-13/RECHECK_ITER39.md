# P4.13 / P6.18 recheck — iter39 (2026-07-02)

## Cross-seam fix: Control URLField → CharField (Blocker 2)

| Source | `models.py:41` | ws:// registration |
|--------|------------------|-------------------|
| **before** | `URLField` — `Enter a valid URL.` | **FAIL** |
| **after** | `CharField(max_length=2048)` + migration `0015` | **PASS** |

Migration applied live: `mcp_connector.0015_mcpserverregistration_url_charfield OK`.

Also added `ws-everything.stub:3003` + `ws-everything.stub` to `MCP_ALLOW_INTERNAL_HOSTS` (control
`.env` + `scripts/mcp_enable_http_via_sandbox.sh` default) so SSRF guard permits in-cluster stub
hostnames. Fixed `mcp_ws_everything_stub.mjs` echo prefix (`Echo: {message}`) for harness parity.

## Gateway ws routing — **LANDED** (CHG-0026, unchanged)

`broker_send_rpc` for websocket; no `mcp_ws_adapter` hot path.

## 4/4 transport ROUNDS=3

```
TRANSPORT_MANIFEST=mcp-parallel/findings/p4-13/TRANSPORT_MANIFEST.zeroshield.json ROUNDS=3 \
  python3 scripts/mcp_sandbox_transport_verify.py
→ SANDBOX_TRANSPORT: PASS (3 round(s))
```

Manifest includes all four slugs: stdio, streamable-http, sse, **websocket** (`ws-everything-stub`).

## Network assertion

`ss -tnp | grep :443` on gateway → **empty** (no direct upstream :443).

## Gates

| Gate | Result |
|------|--------|
| Broker pytest | 98 passed |
| Agent pytest | 44 passed (~250s) |
| Frontend build | ✓ |
| Playwright B1 | ALL ASSERTIONS PASS |
| Playwright B2 | ALL ASSERTIONS PASS |
| Playwright B4 | ALL PASS |
| Transport verify ROUNDS=3 | 4/4 PASS |

## Hive

`cursor-ralph-iter39` on `hive-1782991737290-ylo911`. Claim released on commit.
