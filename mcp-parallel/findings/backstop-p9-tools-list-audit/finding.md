# CHG-0081 — tools/list metadata-scan decisions were UNAUDITED (broke the …→tag→audit chain)

**Change-id:** CHG-0081
**Date:** 2026-07-02
**Severity:** MEDIUM (audit-completeness: a tool-poisoning BLOCK or a secret/PII/IP REDACT on the discovery path was invisible to the MCPEvent audit/SIEM trail)
**Area:** HARDEN 1.4 — the "…→tag→AUDIT" end of the per-call chain, for tools/list
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (+ `tests/test_mcp_tools_list_audit.py`)
**Whose work it touches:** the org tools/list path + `_scanned_tools_list_response` (CHG-0077); the gateway audit pipeline.

## How it was found (devil's-advocate on the audit end)

CHG-0077 scans tools/list tool descriptions and masks/blocks a poisoned metadata leak. The mandate's chain
is authz→minimize→scan+redact→tag→**audit**. Checking the audit end: `_scanned_tools_list_response`
had ZERO `_record_gateway_event` calls, while the tools/call path audits heavily.

## Gap

When the gateway REDACTED a secret/PII/internal-IP in a tool description, or BLOCKED a poisoned tools/list
(tool-poisoning / encoded-exfil detected), it recorded **no** MCPEvent. So the most forensically-important
discovery-path security events were invisible to audit/SIEM — asymmetric with tools/call (which audits
block/redact/allow) and with the ext-proxy audit (CHG-0068/0070).

## Fix

`_scanned_tools_list_response` now calls the best-effort `_record_gateway_event` when the metadata scan
BLOCKED or REDACTED (block XOR redact), with `tool_name="tools/list"`, `reason="tools_list_metadata_scan"`,
the compliance tags, the scan findings, and a threaded per-request correlation id
(`_mcp_request_correlation_id`, passed as a new `request_id` param from both org sub-paths). A fully-CLEAN
tools/list is NOT audited (avoids per-discovery noise). Fire-and-forget / no-op without org (same
`_record_gateway_event` used everywhere → identical persistence + EnforcementEvent bridge).

## Behaviour after fix (verified)

- Secret/internal-IP in a tool description → **audited** `decision=redact` with `compliance_tags=[INFRA,SECRET]`
  + the request-id.
- Encoded-exfil poisoned metadata → **audited** `decision=block`.
- Benign tools/list → **no** audit event (no noise).

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_tools_list_audit.py -q   # 3 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                 # 1363 passed, 0 failed
```
Broker regression: `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- The `initialize` result scan (CHG-0080) is on the ext-proxy path, which audits result block/redact via
  `_ext_audit` (CHG-0068/0070) — already covered; confirm the initialize decision surfaces there in a
  future audit-completeness sweep.
- A CLEAN tools/list is intentionally not audited; if per-discovery "allow" telemetry is later wanted, add
  a low-priority monitor event.
