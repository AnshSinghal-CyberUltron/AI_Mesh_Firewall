# CHG-0121 — broker did not forward X-Request-ID to the sandbox AGENT (last trace hop); agent didn't log it

**Change-id:** CHG-0121
**Date:** 2026-07-03
**Severity:** LOW (observability / tracing completeness — the LAST hop of the audit-trail correlation, not a leak).
**Area:** HARDEN THE ARCHITECTURE — Phase-3 monitoring / metrics / TRACING; closes the CHG-0052 residual and completes the correlation-id chain begun by CHG-0050/0051/0052/0120.
**Files:** `services/mcp-broker/src/sandbox/routes.py` (`_post_agent_rpc` + `_forward_sandbox_rpc` call site); `services/mcp-broker/sandbox-image/agent/main.py` (`/rpc` reads + logs `X-Request-ID`); `services/mcp-broker/tests/test_agent_ready_retry.py` (+2 tests); `services/mcp-broker/sandbox-image/agent/tests/test_rpc_unified.py` (+1 test).
**Whose work it touches:** the broker sandbox-RPC forward (`routes._post_agent_rpc`) + the in-container agent `/rpc` handler.

## Root cause

The correlation-id chain was: gateway audit + X-Request-ID header (CHG-0050) → broker propagate + LOG
(`_forward_sandbox_rpc`, CHG-0051/0052) → **[BREAK]** → sandbox agent. The broker's `_post_agent_rpc` was invoked
with NO `request_id` and did `client.post(url, json=payload)` with **no headers**, so the correlation id never
reached the in-container agent — and the agent's `/rpc` handler neither read nor logged it. The last hop
(broker → in-container agent) was untraced — exactly the CHG-0052 residual ("forward X-Request-ID to the sandbox
AGENT + agent-log").

## The fix (CHG-0121)

1. **Broker forwards it** — `_forward_sandbox_rpc` passes its `request_id` (from the inbound `X-Request-ID` header,
   CHG-0051) to `_post_agent_rpc`, which sets it as an `X-Request-ID` header on the agent POST
   (`headers = {"X-Request-ID": request_id} if request_id else None` — omitted entirely when absent, no spurious
   empty header).
2. **Agent logs it** — the sandbox agent `/rpc` handler reads `X-Request-ID` (FastAPI `Header`) and logs ONE line at
   entry with **SAFE metadata ONLY**: `org / server_slug / transport / method / jsonrpc_id / request_id` — NEVER
   params/args/env/upstream (which can carry PII/secrets — mirrors the broker CHG-0052 log hygiene).

The trace is now continuous: gateway audit ↔ broker log ↔ **sandbox agent log**, all correlated by X-Request-ID.

### Byte-level truth

- broker: `_post_agent_rpc(..., request_id="trace-agent-7")` → the agent POST carries `{"X-Request-ID":
  "trace-agent-7"}`; with no `request_id` → `headers is None` (no header).
- agent: `POST /rpc` with `X-Request-ID: trace-agent-7` logs
  `agent rpc org=test-org server=srv transport=stdio method=tools/list jsonrpc_id=1 request_id=trace-agent-7` — and
  does NOT log the command/args (`npx` / `secret-pkg` absent).

## Verification (all green)

```
cd services/mcp-broker
PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_agent_ready_retry.py -o asyncio_mode=auto -q   # 5 passed
PYTHONPATH=…/shared:…/sandbox-image ./.venv/bin/python -m pytest sandbox-image/agent/tests/test_rpc_unified.py -o asyncio_mode=auto -q   # 7 passed
./.venv/bin/python -m pytest tests -q -k "not websocket"                                              # 157 passed
```

2 new broker tests (forwards the header / no header when absent) + 1 new agent test (logs the request_id, does NOT
log the command/args). Gateway unaffected (broker + agent only — the id flows FROM the gateway, CHG-0120, TO here).

## Scope / honesty note

Tracing-completeness fix — no bytes changed on the wire, no PII-text egress delta; the request_id-in-agent-log +
no-args-leaked byte assertions are authoritative. Completes the correlation-id chain (CHG-0050/0051/0052/0120) — the
last untraced hop is now covered. The agent-side change takes effect on the next sandbox-image rebuild (the agent
runs in the container). STILL OPEN on item 13 (INFRA, host-blocked): OTEL/Jaeger distributed tracing; PG/Redis backup
verification. Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
