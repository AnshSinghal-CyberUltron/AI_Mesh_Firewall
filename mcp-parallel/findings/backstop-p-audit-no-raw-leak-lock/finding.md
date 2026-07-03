# CHG-0139 — regression-lock: the MCP audit record never contains the raw PII/secret value (leak-at-rest)

**Change-id:** CHG-0139
**Date:** 2026-07-03
**Severity:** LOW (test-only regression-lock — **no production code change**). Locks a verified-but-untested 1.4-at-rest invariant: the redacted-scan's AUDIT record must not embed the raw secret/PII it redacted, so the persisted MCPEvent trail can't become a leak-at-rest.
**Area:** HARDEN 1.4 — compliance tagging / audit ("tag results, enforce by tag, audit"). Complements the redaction (egress-bytes) tests.
**Files:** `gateway/ai_mesh_gateway/tests/test_mcp_audit_no_raw_leak.py` (new, +6 parametrized). No production code change.
**Whose work it touches:** locks an invariant of the owning-session scan orchestrator (`McpFinding` / `scan_mcp_payload`) + audit path (`_record_gateway_event`).

## Context — the invariant (verified sound)

`_record_gateway_event` serializes into the `MCPEvent` audit row: `decision`, `policy_reason`, `compliance_tags`,
`scan_findings` (`McpFinding.to_finding_dict()`), and `metadata`. I byte-probed the redact path on a battery of
secrets/PII (AWS key, email, SSN, GitHub PAT, Stripe key, internal IP):

- `McpFinding` carries only `entity_type`, `score`, `start`/`end` **offsets**, `direction`, `tier`,
  `threat_type`, `detail` (= *type names*, e.g. `"Matched: email, aws_access_key"`), and `matched_kinds`
  (*type names*) — **no raw-value field**.
- `compliance_tags` are tag CODES (`SECRET`, `GDPR`, `PII`; normalized to catalog codes at the control-plane
  persistence layer via `tasks.py` `_normalize_compliance_tags`).
- `metadata` from the callers is `{transport, enforced_at, scan_trace, ...}` — no raw content.

So the audit record never contains the raw value; the redacted **result** drops it too. Byte-verified: the raw
value is absent from `json.dumps({findings, compliance_tags, metadata})` for every class.

## The gap (coverage, not behavior)

There was **no test** asserting the audit-serializable output carries no raw value. The redaction tests assert the
**egress RESULT bytes** drop the secret — a *different* surface than the **audit RECORD bytes**. A future change —
a `McpFinding.detail` that quoted the matched span for debugging, or a caller threading raw content into
`metadata` — would silently leak the exact secret/PII into the audit DB (leak-at-rest) with a green suite.

## The lock (CHG-0139)

`test_mcp_audit_no_raw_leak.py` runs `scan_mcp_payload` (redact) on each of a secret/PII battery and asserts the
raw value is **absent** from `json.dumps({findings: to_finding_dict(), compliance_tags, metadata})`, while also
asserting the scanner **did** detect it (non-vacuous) and the redacted result drops it.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_audit_no_raw_leak.py -q   # 6 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                  # 1926 passed, 0 failed
```

## Scope / honesty note

Test-only regression-lock; no behavior change. Covers the tier-1 detector output (the redact path); the raw-value
absence holds structurally because `McpFinding` has no raw-value field and callers pass only type/trace metadata.
Does not change the host-blocked live-stress status (items 14–19). Partial coverage is not completion.
