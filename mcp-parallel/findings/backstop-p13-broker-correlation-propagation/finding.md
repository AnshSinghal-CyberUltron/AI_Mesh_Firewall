# BACKSTOP finding — correlation id not propagated to the broker/sandbox (CHG-0051)

- **Item:** G4 item 13 ("Monitoring + metrics + tracing wired"). Completes the CHG-0050
  follow-up (end-to-end request tracing).
- **Change-id:** CHG-0051 (2026-07-02)
- **Severity:** LOW-MEDIUM — observability/tracing gap (not a leak).

## Title
CHG-0050 established a per-request correlation id (from `X-Request-ID`) in the gateway's
MCP audit events, but `broker_send_rpc` sent NO correlation id to the broker — so the
broker + sandbox logs for a tool call could not be tied back to the gateway's MCPEvent
audit (the trace ended at the gateway boundary).

## Fix
Out-of-band `X-Request-ID` header propagation (does not touch the JSON-RPC payload
schema), threaded along the tool-call path:
- `mcp_sandbox_client._request_with_503_retry(..., extra_headers=None)` — merges caller
  tracing headers with the broker auth headers (built once, reused across 503 retries).
- `mcp_sandbox_client.broker_send_rpc(..., correlation_id="")` — when set, sends
  `X-Request-ID: <id>` to `POST {broker}/v1/sandbox/{org}/rpc` (no header when unset, so
  no empty/None header pollution).
- `mcp_proxy._adapter_forward(..., correlation_id="")` — passes it to `broker_send_rpc`.
- `org_mcp_jsonrpc` tool-call site — passes `correlation_id=_req_id` (the CHG-0050
  correlation id) into `_adapter_forward`.

Now a remote-transport (streamable-http / sse / websocket) tool call carries the
gateway's correlation id all the way to the broker and sandbox, so their logs correlate
with the MCPEvent audit for the same call.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_client.py -q`
  → 13 passed, incl. 2 new: `broker_send_rpc(correlation_id="trace-xyz-123")` sends
  `X-Request-ID: trace-xyz-123` (alongside the broker auth header); with no
  `correlation_id`, NO `X-Request-ID` header is sent.
- Broad sweep `ai_mesh_gateway/tests` → 1117 passed, 0 failed.

## Scope / documented follow-ups (not in this change)
- The `stdio` branch of `_adapter_forward` uses `mcp_stdio_adapter.send_jsonrpc` (not
  `broker_send_rpc`), so stdio tool calls don't yet carry the header — a follow-up.
- The `tools/list` adapter site + `internal_tools_call` + the early per-key authz audit
  sites in `org_mcp_jsonrpc` (all before `_req_id` is defined) still pass no id — mechanical
  follow-ups (thread `_req_id` earlier / into those sites).
- The broker/sandbox agent should LOG the received `X-Request-ID` for the trace to be
  visible end to end (broker-side change).
- OTEL/Jaeger + PG/Redis backup remain infra. Item 13 stays `[ ]`.
