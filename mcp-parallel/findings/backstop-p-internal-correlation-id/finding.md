# CHG-0120 — internal (chat-pipeline) routes dropped the X-Request-ID correlation id

**Change-id:** CHG-0120
**Date:** 2026-07-03
**Severity:** LOW (observability / tracing completeness — audit-trail correlation, not a leak).
**Area:** HARDEN THE ARCHITECTURE — Phase-3 monitoring / metrics / TRACING; completes the CHG-0050/0051/0052 correlation-id propagation for the internal routes.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`internal_tools_call`, `internal_discover_tools`, `_scan_internal_tools_list`); `gateway/ai_mesh_gateway/tests/test_mcp_internal_correlation_id.py` (new, 3 tests); `gateway/ai_mesh_gateway/tests/test_mcp_inbound_redact_audit.py` (test `_fwd` mock signature).
**Whose work it touches:** the owning-session internal routes; completes CHG-0050/0051/0052.

## Root cause

CHG-0050/0051 made `org_mcp_jsonrpc` + the bare REST route propagate the inbound `X-Request-ID` into every MCPEvent
AND the broker `_adapter_forward` hop (which forwards it out-of-band via `broker_send_rpc` → the broker logs it,
CHG-0052) — so an MCP tool call traces gateway → broker → sandbox. But the INTERNAL (chat-pipeline) routes were the
last untraced hop:

- `internal_tools_call` recorded ALL its audit events (inbound block/redact CHG-0109; sandbox + httpx result
  block/redact CHG-0105/0106) with **no `request_id`**, and called `_adapter_forward` with **no `correlation_id`**.
- `internal_discover_tools` recorded its metadata-scan audit (CHG-0108) with **no `request_id`** and forwarded to
  the adapter with **no `correlation_id`**.

So a chat-pipeline tool call / tool-sync trace **ended at the internal gateway boundary** — the CHG-0050 residual
("thread _req_id into internal_tools_call").

## The fix (CHG-0120)

Both routes now compute `_mcp_request_correlation_id(request, 1)` once at entry (prefers the inbound `X-Request-ID`,
bounded to 200 chars; falls back to the JSON-RPC id, else ""), and:
- thread it as `request_id=` into ALL `_record_gateway_event` calls — including the nested `_scan_internal_result`
  closure (which captures it from the enclosing scope) and the `_scan_internal_tools_list` helper (which gained a
  `request_id` param, also passed on to `_scanned_tools_list_response`);
- pass it as `correlation_id=` to `_adapter_forward` (already propagated to the broker via `broker_send_rpc`,
  CHG-0051 → broker log, CHG-0052).

The trace chain is now continuous on the chat-pipeline path (gateway audit ↔ broker log ↔ sandbox), at parity with
the org / REST paths.

### Byte-level truth

With an inbound `X-Request-ID: trace-corr-99`:
- every internal audit `request_id` == `trace-corr-99`;
- `_adapter_forward(correlation_id=)` == `trace-corr-99`.
- With NO `X-Request-ID` header, the correlation id falls back to the JSON-RPC id (`"1"`) — never crashes.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_internal_correlation_id.py -q   # 3 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                       # 1756 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"        # 155 passed
```

3 new tests: `internal_tools_call` — every audit event + the adapter hop carry the X-Request-ID; `internal_discover_tools`
— same; missing header → falls back to the JSON-RPC id, no crash. A pre-existing CHG-0109 test's `_fwd` mock signature
was updated to accept the new `correlation_id` kwarg (the mock, not the behaviour, was stale).

## Scope / honesty note

Tracing-completeness fix — no bytes changed on the wire, no PII-text egress delta, so aidefence is not the applicable
oracle; the request_id-threaded byte assertions are authoritative. Completes CHG-0050/0051/0052 (correlation-id
propagation) for the internal path — the last untraced MCP hop. STILL OPEN on item 13 (INFRA, host-blocked):
OTEL/Jaeger distributed tracing; PG/Redis backup verification. Does not change the host-blocked live-stress status
(items 14–20). Partial coverage is not completion.
