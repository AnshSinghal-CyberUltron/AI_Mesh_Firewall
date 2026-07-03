# CHG-0106 — internal LEGACY direct-httpx path scanned only `result.content` (error-frame / structuredContent leak)

**Change-id:** CHG-0106
**Date:** 2026-07-03
**Severity:** MEDIUM (fail-open 1.4 leak on the `MCP_HTTP_VIA_SANDBOX=0` legacy fallback — a weaker leak posture than the default sandbox path; NOT the default production route).
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS (byte-verified, fail-closed), internal (chat-pipeline) route.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_scan_internal_result` inside `internal_tools_call`); `gateway/ai_mesh_gateway/tests/test_mcp_internal_http_result_scan.py` (new, 5 tests).
**Whose work it touches:** the owning-session proxy `internal_tools_call`; completes the CHG-0105 follow-up (its explicitly-flagged residual).

## Root cause

CHG-0105 closed the internal SANDBOX branch (stdio/websocket + the default HTTP-via-sandbox path). Its
flagged RESIDUAL was the LEGACY direct-httpx path: `_scan_internal_result` (the outbound result scanner
reached when `MCP_HTTP_VIA_SANDBOX=0`, i.e. `_is_sandbox_routed`→False — a debug fallback, since the
default routes ALL four transports through the per-org sandbox) scanned only `result_obj.get("content")`:

```python
result_obj = resp_obj.get("result")
result_content = result_obj.get("content") if isinstance(result_obj, dict) else None
if result_content is None:
    return JSONResponse(content=resp_obj, status_code=200)   # <-- UNSCANNED egress
```

So when `result.content` was absent, the reply egressed **RAW**:
- a bare **error frame** — a secret / PII / internal-IP in `error.message`;
- a **`structuredContent`-only** result (no `content` array) — nested secrets/PII;
- a bare string/list result.

It also **silently swapped** masked content with **no audit event** on the redact (only the block was audited).

### Byte-level truth (pre-fix, legacy path `MCP_HTTP_VIA_SANDBOX=0`) — driving the REAL `internal_tools_call`

| Case | Egress (pre-fix) |
|------|------------------|
| error frame `key=AKIAIOSFODNN7EXAMPLE host 10.1.2.3 user bob.jones@corp.example` | **sec: LEAK, ip: LEAK, email: LEAK** |
| `result.structuredContent` = `{secret: AKIA…, ssn: 123-45-6789}` | **sec: LEAK, ssn: LEAK** |
| `result.content` text (control) | masked (scanner works when content present) |

## The fix (CHG-0106)

`_scan_internal_result` now does an **error-envelope aware WHOLE-result scan**, mirroring the sandbox branch
(CHG-0105) and the org path (CHG-0091):

```python
scan_target = resp_obj.get("result") if "result" in resp_obj else resp_obj
scanned, out_blocked, out_tags, out_findings, meta = await _scan_tool_result_floor(scan_target, ...)
# block -> JSON-RPC error + audit(block); else swap masked payload + audit(redact, transport="internal")
```

`_scan_tool_result_floor` scans "the ENTIRE result/error/notification (dict content/structuredContent, list,
or str)" (mcp_proxy.py:1341) — so `content`, `structuredContent`, bare string/list, and a bare error envelope
are all covered, with the full hardened machinery (secret/PII/IP redaction, render-leak neutralization
CHG-0096–0100, cross-block split-check, block-count cap). The redact is now **audited** (`decision="redact"`,
`transport="internal"`), closing the prior audit omission.

### Byte-level truth (post-fix, legacy path)

- error frame → `key=AKIA****MPLE host [INTERNAL_IPV4_REDACTED] user b***@c***.example`
- `structuredContent` → `"secret":"***","ssn":"***-**-6789"`
- content control → `contact bob.jones@corp.example` → `b***@c***.example` (still masked, no regression)
- benign → `the weather in Paris is sunny` (preserved, no block/redact audit)

### Independent oracle (aidefence)

`aidefence_has_pii` on the error-frame egress JSON: **true** on the raw (pre-fix) bytes, **false** on the fixed
bytes — corroborates the byte-level assertion without relying on the same regexes under test.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_internal_http_result_scan.py -q   # 5 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                         # 1649 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"          # 108 passed
```

5 new tests drive the REAL `internal_tools_call` legacy httpx path (`_is_sandbox_routed` forced False,
hermetic — no global env mutation): error-frame secret+IP+PII masked; structuredContent-only masked + redact
audited; content control still masked (no regression); redact audited; benign preserved. Existing
internal-path tests still pass.

## Scope / honesty note

Closes the CHG-0105 residual. This path is reached **only** on the legacy `MCP_HTTP_VIA_SANDBOX=0` fallback;
the default production internal route sends all transports through the sandbox (CHG-0105) and was already
scanned. Still a real fix: a debug fallback must not be a weaker leak posture than production, and the redact
is now audited. Result-egress redaction is now at full parity across the three internal-result paths
(org `_scan_internal_result`-equivalent CHG-0091 / sandbox CHG-0105 / legacy-httpx CHG-0106). Does not change
the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
