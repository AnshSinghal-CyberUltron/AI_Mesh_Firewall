# BACKSTOP hardening — ext_mcp_proxy now audits its enforcement decisions (CHG-0068)

- **Item:** G3 item 9 (gateway ... audit) / 1.4 "the full per-call chain authz→minimize→scan+redact→tag→AUDIT".
- **Change-id:** CHG-0068 (2026-07-02)
- **Type:** Audit-completeness gap (security decisions on the external-passthrough path were unrecorded).

## Gap
`ext_mcp_proxy` (the tenant-facing external MCP passthrough, `/v1/mcp/ext-proxy/{host}/{path}`) recorded
NO audit events for ANY of its decisions — it has ZERO `_record_gateway_event` calls, while every other
MCP path (`org_mcp_jsonrpc`, `org_mcp_tool_call`, `internal_tools_call`) audits heavily. So on the
external-server surface (an untrusted attack surface: credential-exfil attempts, PII egress, SSRF):
  * a blocked credential-in-args,
  * a blocked / redacted PII tool result,
  * a blocked SSRF target,
were all INVISIBLE in the MCPEvent audit trail. An operator could not see that an org made external
tool calls, nor that the gateway thwarted an attack on that path — breaking the mandate's
`...→tag→audit` chain for external tool usage and any compliance/forensic review.

## Fix — `gateway/ai_mesh_gateway/mcp_proxy.py`
Added a small local async helper `_ext_audit(decision, reason, *, tool, tags, findings)` in
`ext_mcp_proxy` that calls the existing best-effort `_record_gateway_event` with the caller's org, a
`server_slug="ext:<host>"` (the external host is not a registered org server), the tool name, decision,
reason, latency, and compliance tags / findings. It is fire-and-forget (no added latency) and a safe
no-op when unauthenticated (`_record_gateway_event` returns early on an empty org). Wired at the
enforcement decisions:
  * SSRF block (CHG-0065)            → decision=block  reason=ssrf_blocked
  * inbound credential block          → decision=block  reason=credential_blocked_inbound
  * outbound result block (tags)      → decision=block  reason=pii_blocked_outbound
  * outbound result redaction         → decision=redact reason=pii_redacted_outbound

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q`
  → 49 passed. New: a result-redaction records a `decision=redact` event with `org_slug` + `server_slug`
  starting `ext:`; an SSRF block records `block`/`ssrf_blocked`; a credential-in-args block records
  `block`/`credential_blocked_inbound`; an UNAUTHENTICATED call (no org) is a safe no-op (still forwards
  + redacts, records nothing). The existing 45 ext-proxy tests still pass (audit is a no-op without an
  org context, so they were unaffected).
- Full sweep `ai_mesh_gateway/tests` → 1252 passed, 0 failed.

## Residual (follow-up, NOT changed — one item/iteration)
The "allow" (successful call) path and the infra-error withholds (non-200 / non-JSON body-withheld
[CHG-0061], upstream-response-too-large [CHG-0064]) are not yet audited — those are lower-priority
(normal success / already-logged infra errors). Auditing them would complete the ext-proxy audit trail.
