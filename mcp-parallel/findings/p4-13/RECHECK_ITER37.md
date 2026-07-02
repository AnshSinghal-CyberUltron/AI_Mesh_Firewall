# P4.13 / P6.18 recheck — iter37 (2026-07-02)

## Claude lands recheck

### 1. Gateway ws routing — **LANDED** (git d6ab1ae7 CHG-0026 + rebuilt live)

| Source | streamable-http / sse / websocket | Legacy `mcp_ws_adapter` hot path |
|--------|-----------------------------------|----------------------------------|
| **git** (`mcp_proxy.py` L1974) | `broker_send_rpc` for all three | **removed** (comment-only ref) |
| **live** (post-rebuild `ai_mesh_firewall-gateway-1`) | `broker_send_rpc` L1974 | **absent** |

**Action taken (iter37):** `docker compose build gateway && docker compose up -d gateway` — live now matches git.

### 2. Control URLField ws support — **NOT LANDED**

| Source | `models.py:41` |
|--------|----------------|
| **git** | `models.URLField(...)` — rejects `ws://` |
| **live** (`ai_mesh_firewall-control-1`) | same `URLField` |

Registration probe: `scripts/mcp_register_transport_servers.py` →  
`websocket: FAILED ERR:400:{'url': ['Enter a valid URL.']}`

`TRANSPORT_MANIFEST.zeroshield.json` has **no websocket slug** (3/4 configured).

**Cursor doc:** `docs/mcp/CLAUDE_WS_BLOCKERS.md` — exact patch spec for URLField → CharField.

---

## 3-transport ROUNDS=3 (auto-stubs)

Harness: `TRANSPORT_MANIFEST=.../TRANSPORT_MANIFEST.zeroshield.json ROUNDS=3 scripts/mcp_sandbox_transport_verify.py`

**Enhancement (iter37):** harness auto-calls `scripts/mcp_transport_stubs_up.sh` (`AUTO_STUBS_UP=1` default);
unregistered transports (websocket) count as **BLOCKED**, not FAIL.

| Transport | R1–R3 | Notes |
|-----------|-------|-------|
| stdio | PASS | tools=13, echo ok |
| streamable-http | PASS | tools=12, echo ok |
| sse | PASS | tools=12, echo ok |
| websocket | BLOCKED | URLField — no manifest slug |

**Verdict:** `PASS_PARTIAL (3/4)` — **3 configured transports green 3×**; no regression vs iter35/36.

## Network assertion (ss)

Gateway container: **no :443** egress (`ss -tnp | grep :443` empty).

## P4.13 / P6.18 checkbox

**Cannot mark `[x]`** — 1/2 ws seams remain (URLField blocks registration + 4-transport e2e).
Gateway isolation for ws is **code+deploy complete**; end-to-end ws blocked on control model only.

## Unblock criteria (remaining — Claude-owned)

1. ~~`mcp_proxy.py` websocket → `broker_send_rpc`~~ **DONE** (CHG-0026 + iter37 rebuild).
2. Control `MCPServerRegistration.url` accepts `ws://` / `wss://` — see `docs/mcp/CLAUDE_WS_BLOCKERS.md` §Blocker 2.
3. Register `ws-everything-stub`; Cursor runs `ROUNDS=3` **4/4** transports → then `[x]`.
