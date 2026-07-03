# CHG-0095 — ext-proxy infra-error WITHHOLD/redact decisions were not audited

**Change-id:** CHG-0095
**Date:** 2026-07-03
**Severity:** MEDIUM (audit-completeness; fail-closed content blocks invisible to the MCPEvent trail — the `…→tag→AUDIT` chain, item 9).
**Area:** HARDEN item 9 (gateway audit) + the full 1.4 per-call chain `…→tag→AUDIT`, external MCP passthrough.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`ext_mcp_proxy`); `gateway/ai_mesh_gateway/tests/test_mcp_ext_withhold_audit.py` (new).
**Whose work it touches:** the owning-session ext passthrough audit (CHG-0068/0070) — closes the residual those left: "the allow path + infra-error withholds (non-200/non-JSON CHG-0061, response-too-large CHG-0064) not yet audited."

## Root cause

`ext_mcp_proxy` records audit events for its SSRF block, credential-arg block, and PII result block/redact (CHG-0068/0070) — but several **fail-closed enforcement decisions recorded NOTHING**, so a withheld/redacted response was invisible in the MCPEvent audit trail (unlike every other MCP path):

- upstream **response too large** withhold — SSE (`_mcp_upstream_too_large_response`, CHG-0064) and non-SSE;
- **non-JSON body** withhold + redact (CHG-0061 text-body floor);
- JSON-RPC **error content** withhold + redact;
- **non-200 body** withhold + redact (CHG-0061 whole-body floor);
- **request body too large** DoS-guard reject.

A withhold is a security-relevant decision (content blocked from egress); not auditing it breaks the `…→tag→AUDIT` chain precisely for the fail-closed cases.

## The fix (CHG-0095)

Added `_ext_audit(...)` at every previously-silent enforcement site (fire-and-forget, no latency; no-op when unauthenticated) with a stable reason:
- `block`/`response_too_large` (SSE + non-SSE),
- `block`/`text_body_withheld` + `redact`/`text_body_redacted`,
- `block`/`error_content_withheld` + `redact`/`error_content_redacted`,
- `block`/`nonok_body_withheld` + `redact`/`nonok_body_redacted`,
- `block`/`request_too_large`.

Each carries the tool name + compliance tags + findings where available. No behavior change to the responses themselves — purely additive audit records.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_ext_withhold_audit.py -q   # 5 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                  # 1558 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"  # 108 passed
```

5 new tests drive the REAL `ext_mcp_proxy` and assert `_record_gateway_event` fired with the right `(decision, reason)`: response-too-large on the JSON path, response-too-large on the SSE path, non-JSON text-body redaction, non-200 body redaction, and request-too-large. The text-body test also asserts the email is masked on egress (redaction still works; the audit is the addition).

## Residual

The domain-not-in-allowlist 403 reject (line ~1515) is emitted BEFORE the `_ext_audit` closure is defined and is a static-allowlist input reject (high-volume-noise potential); left unaudited by design for now. Noted, not fixed.

## Scope / honesty note

Audit-completeness fix — no content leak, so no aidefence oracle; the proof is that each fail-closed decision now emits its audit event (asserted on the real handler). Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
