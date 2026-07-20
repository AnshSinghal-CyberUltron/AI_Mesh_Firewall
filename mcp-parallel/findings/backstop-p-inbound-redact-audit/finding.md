# CHG-0109 — INBOUND arg redaction was not audited on the internal / bare-REST / ext paths

**Change-id:** CHG-0109
**Date:** 2026-07-03
**Severity:** LOW–MEDIUM (audit-completeness / compliance-visibility gap — NOT a data leak: the egress bytes were already correctly redacted; the REDACTION itself was invisible to the audit/SIEM trail).
**Area:** HARDEN 1.4 — compliance tagging "tag INPUTS + results, enforce by tag, AUDIT" + the per-call chain …→scan+redact→tag→**AUDIT**.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`internal_tools_call`, `org_mcp_tool_call`, `ext_mcp_proxy` inbound-redact branches); `gateway/ai_mesh_gateway/tests/test_mcp_inbound_redact_audit.py` (new, 5 tests).
**Whose work it touches:** the owning-session proxy's three non-main tool-call inbound paths; inbound twin of CHG-0081 (REST result-redact audit) / CHG-0106 (internal result-redact audit).

## Root cause

When a tool call's ARGUMENTS carry PII/IP that the gateway MASKS (server `scan_action="redact"`, not a hard
block) before forwarding to the MCP server, the main `org_mcp_jsonrpc` path folds that into its single per-call
audit event (`_was_redacted` → `decision="redact"`, line ~4034). But the other three tool-call paths swapped the
masked args in **silently**:

```python
# internal_tools_call / org_mcp_tool_call / ext_mcp_proxy — BEFORE
if scanned_args is not arguments:
    arguments = scanned_args          # forward masked args … but NO _record_gateway_event
```

So an INPUT redaction — a user's PII masked before it egressed to the MCP server, a compliance-relevant event —
was **invisible** to the audit/SIEM trail. This is asymmetric with:
- the inbound BLOCK branch (`pii_blocked_inbound`), which IS audited on all paths, and
- the result-side REDACT audits (CHG-0081 REST / CHG-0106 internal), which record `decision="redact"` outbound.

### Byte-proven reachable (pre-fix) — driving the REAL `internal_tools_call`

A `default_scan_action="redact"` server + args `{"note": "email bob.jones@corp.example please"}`:
- forwarded args → `{"note": "email b***@c***.example please"}` (the email WAS masked — redaction is real), yet
- `_record_gateway_event` call count → **0** (the redaction was never audited).

## The fix (CHG-0109)

Each of the three paths now records, on inbound redaction (not blocked):

```python
if scanned_args is not arguments:
    arguments = scanned_args
    await _record_gateway_event(
        org_slug=…, server_slug=…, tool_name=…,
        decision="redact", reason="pii_redacted_inbound",
        metadata={"transport": …, "enforced_at": "gateway", **scan_meta_in},
        compliance_tags=list(in_tags), scan_findings=list(in_findings),
    )
```

Benign args (no redaction) record nothing (findings-only audit paths — no noise). A credential in args still
force-BLOCKS (records `pii_blocked_inbound`, never a redact). NOTE: the `ext_mcp_proxy` edit is defense-in-depth
parity — that path scans args with `enabled_info=None` → `"tag"` → no inbound redaction occurs under current
config, so its branch is not config-reachable today; it is wired for parity if ext ever passes a redact policy.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_inbound_redact_audit.py -q   # 5 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                    # 1673 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"     # 120 passed (unaffected)
```

5 tests drive the two config-reachable paths END-TO-END (real orchestrator redaction via a
`default_scan_action="redact"` server): internal — email actually masked in the forwarded args AND
`('redact','pii_redacted_inbound')` recorded; internal benign → no audit; internal credential → block-not-redact;
bare-REST redact audited; bare-REST benign → no inbound-redact audit.

### Independent oracle (aidefence)

`aidefence_has_pii` on the forwarded masked args `{"note":"email b***@c***.example please"}` → **false** (the
redaction the audit now records); the raw args are `true`. There is **no raw-vs-fixed EGRESS delta** — this is an
audit-VISIBILITY fix, the egress was already redacted before this change.

## Scope / honesty note

This is an AUDIT-completeness fix, not a leak fix — no bytes changed on the wire; the compliance-relevant input
redaction is now recorded so it is provable in the audit/SIEM trail. Inbound-redact audit is now at parity across
all four tool-call paths (main / internal / REST / ext). The ext branch is defense-in-depth (not config-reachable
today). Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
