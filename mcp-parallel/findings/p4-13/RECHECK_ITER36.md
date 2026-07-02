# P4.13 / P6.18 recheck — iter36 (2026-07-02)

## Claude lands recheck (read-only)

**No change since iter35.** Both Claude-owned ws blockers remain open in **git AND live containers**.

### 1. Gateway ws routing — NOT LANDED

| Source | streamable-http / sse | websocket |
|--------|----------------------|-----------|
| **git** (`mcp_proxy.py`) | `broker_send_rpc` L1974/L1990 | `mcp_ws_adapter` L2017 |
| **live** (`ai_mesh_firewall-gateway-1`) | `broker_send_rpc` L1958/L1974 | `mcp_ws_adapter` L2001 |

Git and container agree — no drift, no rebuild needed.

### 2. Control URLField ws support — NOT LANDED

| Source | `models.py:41` |
|--------|----------------|
| **git** | `models.URLField(...)` — Django rejects `ws://` |
| **live** (`ai_mesh_firewall-control-1`) | same `URLField` |

No serializer/model change landed; `TRANSPORT_MANIFEST.zeroshield.json` has **no websocket slug**.

## 3-transport R1 regression (`ROUNDS=1`)

`TRANSPORT_MANIFEST=TRANSPORT_MANIFEST.zeroshield.json mcp_sandbox_transport_verify.py`

**Precondition:** stubs were down (http/sse containers absent) → first run failed http/sse `tools=0`. After `scripts/mcp_transport_stubs_up.sh` (http+sse+ws stubs on `mcp_sandbox_net_zeroshield`):

| Transport | R1 | Notes |
|-----------|-----|-------|
| stdio | PASS | tools=13, echo ok |
| streamable-http | PASS | tools=12, echo ok |
| sse | PASS | tools=12, echo ok |
| websocket | BLOCKED | server slug missing (URLField) |

**Verdict:** 3/3 gateway transports green on R1 — **no regression** vs iter35 cold-fix; websocket still blocked.

Initial fail (stubs down) is an **environment** issue, not a code regression — document stubs-up as a harness precondition.

## Network assertion (ss)

Gateway container: **no :443** egress to external MCP hosts (`ss -tnp | grep :443` empty).

## P4.13 / P6.18 checkbox

**Cannot mark `[x]`** — 3/4 gateway transports; websocket blocked on two Claude-owned seams (unchanged since iter33).

## Unblock criteria (Claude-owned)

1. `mcp_proxy.py` websocket branch → `broker_send_rpc` (parity with streamable-http/sse).
2. Control `MCPServerRegistration.url` accepts `ws://` / `wss://` (replace or extend `URLField`).
3. Rebuild gateway/control if changed; register `ws-everything-stub`; then Cursor runs `ROUNDS=3` all 4 transports.
