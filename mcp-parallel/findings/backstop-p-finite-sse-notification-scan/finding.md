# CHG-0122 — finite SSE branch didn't scan interleaved server-pushed notifications (mid-call leak)

**Change-id:** CHG-0122
**Date:** 2026-07-03
**Severity:** MEDIUM (1.4 leak — a secret/PII in a server-pushed notification frame interleaved in a finite tools/call SSE egressed raw to the client/LLM).
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS (byte-verified) on the SSE result surface; parity with the non-finite stream (CHG-0098).
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`ext_mcp_proxy` finite SSE branch); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+1 test).
**Whose work it touches:** the owning-session `ext_mcp_proxy` finite SSE branch (parity with the CHG-0098 non-finite stream; extends CHG-0039/0064).

## Root cause

The ext-proxy FINITE SSE branch (a `tools/call` / `resources/*` / `prompts/*` result delivered over `text/event-stream`
— buffered + scanned, CHG-0039/0064) called:

```python
_reframed, _block_info = await _scan_reframe_sse_tool_result(
    sse_bytes.decode(...), tool_name=_ext_tool_name, org_slug="", server_slug="",
    enabled_info=None, actor=None,        # <-- scan_notifications defaulted False
)
```

A finite MCP call's SSE response can **interleave server-pushed notification frames** (`notifications/progress`,
`notifications/message`) BEFORE the final result frame. With `scan_notifications=False`, `_scan_reframe_sse_tool_result`
scans only the `result`/`error` event and re-emits notification events **verbatim**. So a secret / PII in a mid-call
notification egressed to the client/LLM **unredacted** — while the NON-finite stream (`stream_gen`) already scanned
notification `params` (CHG-0098). A finite-vs-non-finite asymmetry on the same untrusted external path.

### Byte-level truth (pre-fix)

A finite tools/call SSE with a leading `notifications/message` frame `{"data":"key AKIAIOSFODNN7EXAMPLE email
bob@corp.example"}` then a benign result → the secret + email egressed **raw** in the re-emitted SSE.

## The fix (CHG-0122)

The finite SSE branch now passes `scan_notifications=True` to `_scan_reframe_sse_tool_result`, so interleaved
notification `params` are scanned (masked, or fail-closed withheld on an unmaskable/encoded-exfil survivor) via the
same result floor as the final result — parity with the non-finite stream. The actual result frame is still delivered;
benign notifications pass through unchanged.

### Byte-level truth (post-fix)

- notification `{"data":"key AKIAIOSFODNN7EXAMPLE email bob@corp.example"}` → the secret + email are **absent** from
  the re-emitted SSE; the `result` frame (`"ok"`) is still delivered.

### Independent oracle (aidefence)

`aidefence_has_pii` on the notification frame JSON: **true** on the raw (pre-fix) frame, **false** on the masked.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q -k sse   # 12 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                      # 1777 passed, 0 failed
```

New `test_ext_finite_sse_scans_interleaved_notification` drives the REAL `ext_mcp_proxy` finite SSE path: the
interleaved notification secret + email are masked, the result frame still delivered. Broker unaffected (gateway-only).

## Scope / honesty note

Closes the finite-vs-non-finite SSE notification-scan asymmetry; server-pushed notification frames are now scanned on
BOTH ext SSE branches (finite CHG-0122 + non-finite CHG-0098). NOTE (matches CHG-0077/0118): the floor masks
secret/PII/internal-IP and blocks encoded-exfil/unmaskable content; free-text prompt-injection phrasing in a
notification message is a separate narrower detection concern. Does not change the host-blocked live-stress status
(items 14–20). Partial coverage is not completion.
