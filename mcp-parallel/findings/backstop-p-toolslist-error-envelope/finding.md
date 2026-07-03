# CHG-0092 — MCP adapter tools/LIST error-envelope leak (the CHG-0091 twin)

**Change-id:** CHG-0092
**Date:** 2026-07-03
**Severity:** MEDIUM (fail-open 1.4 leak on the discovery path; lower per-call secret probability than tools/call, but auth-failure tools/list errors realistically echo a token/URL/PII).
**Area:** HARDEN 1.4 — tool-metadata / result redaction, stdio/websocket transport, tools/list.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`org_mcp_jsonrpc` tools/list adapter fall-through); `gateway/ai_mesh_gateway/tests/test_mcp_adapter_error_envelope_redaction.py` (+3 tools/list tests).
**Whose work it touches:** the owning-session proxy (`mcp_proxy.py`); completes the adapter error-envelope leak class started in CHG-0091 (tools/call) and CHG-0077/0081 (tools/list tools-shaped metadata). This is the exact residual CHG-0091 flagged.

## Root cause

On the stdio/websocket adapter tools/list path, `org_mcp_jsonrpc` scans upstream tool DESCRIPTIONS only when the payload is tools-shaped:

```python
if isinstance(payload, dict):
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("tools"), list):
        ...
        return await _scanned_tools_list_response(payload, ...)   # CHG-0077/0081
return adapter_resp    # <-- everything else returned RAW
```

Any NON-tools-shaped payload — a bare JSON-RPC error envelope (`{"jsonrpc","id","error":{…}}`, the standard response an MCP upstream returns when tools/list fails, e.g. an auth failure), or a malformed result — fell through to `return adapter_resp` **unscanned**. A secret / PII / internal-IP echoed in `error.message` egressed raw.

## The fix (CHG-0092)

Before the fall-through `return adapter_resp`, scan the whole payload through `_scan_tool_result_floor` (the same floor CHG-0091 used for tools/call error envelopes), mirroring the `_scanned_tools_list_response` block/redact/clean contract:
- **redact:** a masked leak → return the redacted envelope (`JSONResponse(content=_tl_s)`);
- **block (fail-closed):** an unmaskable survivor → withhold with a generic error;
- **clean:** unchanged → return the raw `adapter_resp` (no behavior change for benign errors);
- audit the block/redact decision (`reason="tools_list_error_scan"`, `enforced_at="gateway_adapter"`).

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_adapter_error_envelope_redaction.py -q   # 8 passed (5 tools/call + 3 tools/list)
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                                # 1538 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"                # 108 passed
```

3 new tools/list tests drive the REAL handler end-to-end: secret+IP-in-error masked; benign error unchanged; tools-shaped list still scanned via the existing metadata path (guard against the fall-through shadowing CHG-0077).

### Byte-level truth (real handler egress)

| | tools/list `error.message` |
|---|---|
| RAW (pre-scan, `_adapter_forward` output) | `auth failed for user bob.jones@corp.example ssn 987-65-4321` |
| **FIXED** (org_mcp_jsonrpc) | `auth failed for user b***@c***.example ssn ***-**-4321` |

Secret variant: `auth failed for key AKIAIOSFODNN7EXAMPLE at 10.0.0.5` → both the AWS key and the RFC1918 IP masked on egress.

### Independent oracle (aidefence, decoupled from patterns.py)

`aidefence_has_pii`: **false** on the fixed egress, **true** on the raw envelope → an independent detector confirms the fix genuinely masks the leak.

## Investigation note (no gap found)

While writing the tests, a `ghp_…` token in a tool description appeared unmasked — investigated and confirmed a **test-token defect, not a code gap**: the pattern is `\bghp_[a-zA-Z0-9]{36}\b` (exactly 36); the probe token was 25/37 chars. A VALID 36-char github token IS masked by `redact_all` (github_token ∈ PII_PATTERNS → `detect_pii` fires → floor masks). Tests switched to `AKIAIOSFODNN7EXAMPLE` (aws_access_key), which masks standalone reliably.

## Scope / honesty note

This closes the tools/list half of the adapter error-envelope leak class with byte-level + independent-oracle proof and full-suite regression. It does NOT change the host-blocked status of the full 300–500-sandbox live stress (items 14–20). Partial coverage is not completion.
