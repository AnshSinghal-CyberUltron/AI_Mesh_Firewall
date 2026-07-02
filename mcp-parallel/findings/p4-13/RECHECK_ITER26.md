# P4.13 / P6.18 — RECHECK iter26 (PARTIAL — still BLOCKED on §3)

**Iteration:** cursor-ralph-iter26  
**Date:** 2026-07-02

## Verdict

**PARTIAL PROGRESS, still BLOCKED** — Checklist §1 (broker unified route) and §2
(`broker_send_rpc`) are **LANDED in source + live**. §3 (`mcp_proxy` routes all
transports through sandbox) is **NOT landed** — Claude claim
`claude-mcp-p4.13-p6.18` active. Cannot mark P4.13/P6.18/P6.19-P4 `[x]`.

## Live probes (2026-07-02 iter26)

| Probe | URL | HTTP | vs iter25 |
|-------|-----|------|-----------|
| Unified broker RPC | `POST :8311/v1/sandbox/zeroshield/rpc` | **401** (auth required) | was **404** → route EXISTS |
| Stdio broker RPC | `POST :8311/v1/sandbox/zeroshield/stdio/rpc` | **401** | unchanged |
| Broker health | `GET :8311/health` | 200 | unchanged |

## Code grep (gateway + broker)

| Symbol / route | Expected | Actual (iter26) |
|----------------|----------|-----------------|
| `broker_send_rpc` | In `mcp_sandbox_client.py` | **PRESENT** (`:199-256`) — builds `upstream` block + POST `…/sandbox/{org}/rpc` |
| `POST /{org}/rpc` broker route | Unified transport-agnostic | **PRESENT** (`routes.py:296`) |
| `mcp_proxy.py` uses `broker_send_rpc` | All http/sse/ws/stdio via sandbox | **MISSING** — zero references; many direct `httpx.AsyncClient` upstream dials remain |
| `mcp_proxy_direct_httpx` wiring gate | false | **true** (`scripts/mcp_sandbox_transport_verify.py`) |

## Cursor-owned seam (green)

| Component | Gate |
|-----------|------|
| sandbox-agent `/rpc` (all transports) | 41/41 agent tests (via broker venv) |
| broker routes + docker_manager | 95/95 broker lifecycle + 26/26 package tests |
| Combined broker+agent | **138 passed** |

## Unblock (Claude §3)

Land `mcp_proxy` routing per `docs/mcp/gateway-integration-checklist.md` §3
(`_sandbox_forward` / replace direct httpx for streamable-http, sse, websocket),
then:

```bash
ROUNDS=3 python scripts/mcp_sandbox_transport_verify.py
# + tcpdump network assertion per script guide
python scripts/mcp_p10_recursive_gate.py
```
