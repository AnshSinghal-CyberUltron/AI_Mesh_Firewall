# P4.13 / P6.18 — RECHECK iter25 (still BLOCKED)

**Iteration:** cursor-ralph-iter25  
**Date:** 2026-07-02

## Verdict

**BLOCKED** — Gateway wiring not landed. Cannot mark P4.13/P6.18/P6.19-P4 `[x]`.

## Live probes (2026-07-02 iter25)

| Probe | URL | HTTP |
|-------|-----|------|
| Unified broker RPC | `POST :8311/v1/sandbox/zeroshield/rpc` | **404** |
| Stdio broker RPC | `POST :8311/v1/sandbox/zeroshield/stdio/rpc` | **401** (route exists, needs auth) |
| Broker health | `GET :8311/health` | 200 |

## Code grep (gateway + broker)

| Symbol / route | Expected | Actual |
|----------------|----------|--------|
| `broker_send_rpc` | In `mcp_sandbox_client.py` | **MISSING** — only `broker_send_jsonrpc` (`:150-175`) |
| `POST /{org}/rpc` broker route | Unified transport-agnostic | **MISSING** — only `stdio/rpc` (`routes.py:212`) |
| `mcp_proxy.py` direct httpx | No upstream dial for http/sse/ws | **PRESENT** — many `httpx.AsyncClient` calls |

## Cursor prep (iter25)

- Added `scripts/mcp_sandbox_transport_verify.py` — wiring gate + 4-transport e2e (runs when unblocked) + network assertion guide (tcpdump/ss/test-hook).
- Example manifest: `mcp-parallel/findings/p4-13/TRANSPORT_MANIFEST.example.json`
- Checklist updated: `docs/mcp/gateway-integration-checklist.md` §iter25 recheck

## Unblock

Claude must land `docs/mcp/gateway-integration-checklist.md` §1–3, then:

```bash
ROUNDS=3 python scripts/mcp_sandbox_transport_verify.py
# + tcpdump network assertion per script guide
python scripts/mcp_p10_recursive_gate.py
```
