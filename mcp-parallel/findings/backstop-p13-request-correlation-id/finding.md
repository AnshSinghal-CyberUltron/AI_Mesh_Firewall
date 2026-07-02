# BACKSTOP finding — MCP audit events lacked a consistent request correlation id (CHG-0050)

- **Item:** G4 item 13 ("Monitoring + metrics + tracing wired").
- **Change-id:** CHG-0050 (2026-07-02)
- **Severity:** LOW-MEDIUM — observability/tracing gap. Not a leak; it blocks
  end-to-end tracing of a single tool call across the audit trail and services.

## Title
The bare REST tool-call route (`org_mcp_tool_call`) recorded MCPEvent audit events
with NO correlation id, and neither route honored an inbound `X-Request-ID`, so a
single tool call's block/redact/tag decisions could not be correlated across the
audit trail (or with gateway → broker → sandbox logs).

## Reproduction (code trace)
- `_record_gateway_event(..., request_id="")` defaults `request_id` to a fresh
  `mcp-<ms>` timestamp — useless for correlation across events/services.
- `org_mcp_jsonrpc` set `_req_id = str(msg_id)` (the JSON-RPC id — client-controlled
  and repeatable, e.g. `id=1` for every call) and only threaded it into its MAIN
  audit sites; its early per-key authz sites and the whole bare REST route passed no
  `request_id`, so those events fell back to the throwaway timestamp.
- Neither route read the inbound `X-Request-ID` header, so an upstream/distributed
  trace id never reached the MCPEvent audit.

## Fix
New `_mcp_request_correlation_id(request, msg_id=None)`: prefers the inbound
`X-Request-ID` header (bounded to 200 chars against a hostile header), then the
JSON-RPC id, else `""` (→ generated `mcp-<ms>`). Applied:
- `org_mcp_tool_call` (bare REST): `_req_id = _mcp_request_correlation_id(request)`
  threaded into ALL 5 of its audit events (was: zero correlation ids).
- `org_mcp_jsonrpc`: its `_req_id` now uses the helper, so it honors `X-Request-ID`
  over the repeatable JSON-RPC id for its main audit sites.
Robust to a request object without `.headers` (try/except → falls back).

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q`
  → 33 passed, incl. a new integration test driving `org_mcp_tool_call` with an
  `X-Request-ID` header + a blocked tool, asserting the captured audit event's
  `request_id` equals the header; plus unit tests (prefers header / falls back to
  msg_id / empty when neither / bounds a 500-char header to 200 / survives a request
  with no `.headers`).
- Broad sweep `ai_mesh_gateway/tests` → 1115 passed, 0 failed.

## Scope / documented follow-ups (not in this change)
- The early per-key authz audit sites inside `org_mcp_jsonrpc` (before its `_req_id`
  is defined) and `internal_tools_call` still pass no `request_id` — a mechanical
  follow-up (thread `_req_id` earlier / into those sites).
- Propagating the correlation id into `broker_send_rpc` (so the sandbox/broker logs
  carry it) would complete cross-service tracing.
- OTEL/Jaeger distributed tracing (CHG-0020 gap #1) + PG/Redis backup remain infra
  tasks. Item 13 stays `[ ]`.
