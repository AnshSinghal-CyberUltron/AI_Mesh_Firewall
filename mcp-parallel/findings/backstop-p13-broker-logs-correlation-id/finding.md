# BACKSTOP finding — broker didn't log the received correlation id (CHG-0052)

- **Item:** G4 item 13 ("Monitoring + metrics + tracing wired"). Completes the CHG-0051
  propagation by making it OBSERVABLE on the broker.
- **Change-id:** CHG-0052 (2026-07-02)
- **Severity:** LOW-MEDIUM — observability/tracing gap (not a leak).

## Title
CHG-0051 propagated the request correlation id to the broker as an `X-Request-ID`
header, but the broker's RPC route (`sandbox_rpc` / `stdio_rpc` → `_forward_sandbox_rpc`)
neither read nor logged it — in fact the broker RPC path had NO per-call logging at all —
so the propagated trace was invisible on the broker side.

## Fix
`services/mcp-broker/src/sandbox/routes.py`:
- Added a module logger `LOG = logging.getLogger("mcp_broker.sandbox_rpc")` (matching the
  broker's `mcp_broker.<module>` convention).
- Both RPC routes now capture `x_request_id: str | None = Header(default=None,
  alias="X-Request-ID")` and pass it to `_forward_sandbox_rpc(..., request_id=...)`.
- `_forward_sandbox_rpc` logs ONE line at the TOP (before docker/sandbox resolution, so
  even a failed 503 call is traced): `sandbox rpc org=… server=… transport=… method=…
  jsonrpc_id=… request_id=…`.

## PII/secret safety
The log line contains ONLY safe metadata — `org_slug`, `server_slug`, `transport`,
`method`, `jsonrpc_id`, `request_id`. It deliberately does NOT log `params`, `stdio`,
`upstream`, `command`, `args`, or `env`, which can carry PII/secrets (mirrors the
gateway's PII-clean logging verified in CHG-0041). A `-` placeholder is logged when no
`X-Request-ID` is present.

## Verification
- `cd services/mcp-broker && PYTHONPATH="$PWD/src:$PWD/../../shared:$PWD/sandbox-image/agent" .venv/bin/python -m pytest tests/test_broker_auth.py -q`
  → passes, incl. 2 new TestClient tests: a POST to `/v1/sandbox/{org}/rpc` with an
  `X-Request-ID` header logs `request_id=trace-broker-42` + `method=tools/call` and does
  NOT log `params`; a POST with no header logs `request_id=-`. (Both force the clean
  early 503 via `cached_docker_ok=False` so the log-under-test isn't masked by unrelated
  mock-docker resolution / cross-test cached state.)
- Full broker suite → 106 passed, 0 failed. `_post_agent_rpc` retry tests unaffected
  (the change did not touch that function).

## Trace chain now
gateway MCPEvent audit (`request_id`, CHG-0050) → `broker_send_rpc` sends `X-Request-ID`
(CHG-0051) → broker logs `request_id=…` for the forward (CHG-0052).

## Follow-ups (documented)
- Forward `X-Request-ID` from `_post_agent_rpc` to the sandbox AGENT and have the agent
  log it (completes the last hop to the sandbox).
- Gateway stdio branch (`send_jsonrpc`) + tools/list + `internal_tools_call` + early
  `org_mcp_jsonrpc` authz sites still lack the id (CHG-0050/0051 follow-ups).
- OTEL/Jaeger + PG/Redis backup remain infra. Item 13 stays `[ ]`.
