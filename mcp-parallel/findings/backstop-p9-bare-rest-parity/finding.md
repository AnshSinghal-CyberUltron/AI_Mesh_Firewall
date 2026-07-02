# CHG-0082 — two bare-REST parity gaps: tool-call result-redact unaudited + REST tools-list unscanned

**Change-id:** CHG-0082
**Date:** 2026-07-02
**Severity:** MEDIUM (A: audit-completeness on the primary tool-call path; B: tool-poisoning / secret-PII-IP metadata leak on the REST tools endpoint)
**Area:** HARDEN 1.4 — bare-REST parity with `org_mcp_jsonrpc` (scan+redact+AUDIT / tool-description scan)
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (+ `tests/test_mcp_bare_rest_parity.py`)
**Whose work it touches:** the bare-REST routes `org_mcp_tool_call` + `org_mcp_tools_list` (CHG-0031/0077/0081 lineage).

## How it was found (apply the CHG-0081 audit lens + CHG-0077 scan lens to the other REST routes)

After auditing tools/list (CHG-0081), swept the OTHER result-scan callers for the same asymmetries.

## Gap A — bare-REST tool-call result REDACT was unaudited

`org_mcp_tool_call` (the REST `POST .../tools/call`, a primary tenant-facing tool path) audited a result
BLOCK (`decision=block`) but, on a REDACT, swapped the masked content in **silently** — no
`_record_gateway_event`. So a secret/PII/IP MASKED on the bare-REST tool-call path was invisible to
audit/SIEM, asymmetric with the block branch AND with `org_mcp_jsonrpc` (which audits redact). (No data
leaked — the value IS masked; only the audit trail was incomplete.)

## Gap B — REST tools-list endpoint did NOT scan tool descriptions

`org_mcp_tools_list` (the REST `GET .../tools`) FILTERED tools (enabled + per-key allowlist) but never
scanned the tool descriptions, while the JSON-RPC `tools/list` already scans them (CHG-0077). So a
secret/PII/internal-IP (or an encoded-exfil / tool-poisoning payload) in a tool description egressed to the
client on this REST discovery endpoint — a coverage asymmetry with the JSON-RPC path.

## Fix

- **A:** `org_mcp_tool_call` now records a `decision="redact"` gateway event (tags, findings, request-id,
  latency) before swapping in the masked result — mirroring the block branch above it.
- **B:** `org_mcp_tools_list` now runs the filtered tools through `_scan_tool_result_floor`
  (masks maskable leak; blocks unmaskable/encoded-exfil metadata → 403 `tools_withheld`) and audits the
  block/redact decision (`reason="tools_list_metadata_scan"`). A clean list is not audited (no noise).
  Reuses the whole floor chain (CHG-0074/0075/0076/0079). actor=None (descriptions are not actor-scoped).

## Behaviour after fix (verified)

- Bare-REST tool call whose result carries a secret → masked (unchanged) **and now audited** `decision=redact`.
- REST tools-list with a secret+internal-IP in a description → masked, `decision=redact` audited.
- Benign REST tools-list → unchanged, no audit event.

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_rest_parity.py -q   # 3 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                 # 1369 passed, 0 failed
```
Broker regression: `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- The REST tools-list scans BACKEND-sourced descriptions (org-registered tools) — lower risk than
  live-upstream, but now at parity with the JSON-RPC path + defense-in-depth for a compromised registration.
- Injection in a description is detected+tagged (via the floor's tier-1) but not removed under the default
  `tag` posture (same output-injection enforcement residual as CHG-0078).
