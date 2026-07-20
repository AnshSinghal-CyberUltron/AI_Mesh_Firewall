# P4.13 / P6.18 — RECHECK iter24 (still BLOCKED)

**Iteration:** cursor-ralph-iter24  
**Date:** 2026-07-02

## Verdict

**BLOCKED** — Gateway wiring not landed. Cannot mark P4.13/P6.18/P6.19-P4 `[x]`.

## Live probes (2026-07-02)

| Probe | URL | HTTP |
|-------|-----|------|
| Unified broker RPC | `POST :8311/v1/sandbox/zeroshield/rpc` | **404** |
| Stdio broker RPC | `POST :8311/v1/sandbox/zeroshield/stdio/rpc` | 401 (exists, needs auth) |
| Broker health | `GET :8311/health` | 200 ok |

## Code grep (gateway)

| Symbol / route | Expected | Actual |
|----------------|----------|--------|
| `broker_send_rpc` | In `mcp_sandbox_client.py` | **MISSING** — only `broker_send_jsonrpc` |
| `POST /{org}/rpc` broker route | Unified transport-agnostic | **MISSING** — only `stdio/rpc` (`routes.py:212`) |
| `mcp_proxy.py` direct httpx | No upstream dial for http/sse | **PRESENT** — multiple `httpx.AsyncClient` calls to upstream |

## Cursor seam (green)

- Agent unified `POST /rpc` — OK
- Broker stdio path → agent `/rpc` — OK (401 without broker token)
- Multi-org stdio harness 15/15 — OK (P8/P9)

## Unblock

Claude must land `docs/mcp/gateway-integration-checklist.md` §1–3, then re-run 4-transport sandbox + network assertion.
