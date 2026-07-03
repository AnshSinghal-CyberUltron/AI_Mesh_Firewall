# CHG-0123 — internal streamable-http SSE branch parsed per-LINE (returned a leading notification / dropped multi-line results)

**Change-id:** CHG-0123
**Date:** 2026-07-03
**Severity:** LOW (correctness/robustness — **NOT a new leak**: whatever the branch returns is still floor-scanned by `_scan_internal_result`, and a secret split across `data:` lines fails safe to empty. The defect returned the WRONG frame or dropped the result — never leaked one.)
**Area:** SSE parsing parity — the last SSE surface still using a naive per-line split (org SSE = CHG-0093, ext finite SSE = CHG-0122 already reassemble per-event and select the result/error event).
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`internal_tools_call` streamable-http SSE branch, ~L2977); `gateway/ai_mesh_gateway/tests/test_mcp_internal_http_result_scan.py` (+3 tests).
**Whose work it touches:** the owning-session `internal_tools_call` streamable-http branch (parity with org CHG-0093 + ext CHG-0122). Reached only on `MCP_HTTP_VIA_SANDBOX=0` (the legacy direct-httpx internal path; streamable-http is sandbox-routed by default).

## Root cause

The legacy internal streamable-http SSE branch parsed the response PER LINE:

```python
if "text/event-stream" in content_type:
    for line in call_resp.text.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            data_str = line[5:].strip()
            if data_str:
                try:
                    return await _scan_internal_result(json.loads(data_str))
                except json.JSONDecodeError:
                    pass
    return JSONResponse(content={... "Empty SSE response from upstream tools/call" ...})
```

Two robustness bugs:

1. **Leading-notification returned as the response.** An MCP tools/call SSE can interleave server-pushed
   notification frames (`notifications/progress`, `notifications/message`) BEFORE the final result frame.
   The per-line loop returns the FIRST parseable `data:` line — so a leading notification is returned as the
   tool response, and the actual result is never delivered (a notification is surfaced to the chat pipeline
   instead of the tool result). WRONG response.
2. **Multi-line-`data:` result dropped.** Per the SSE spec an event's data is the concatenation of all its
   `data:` field values joined by `\n`. A result delivered across several `data:` lines makes each partial
   line fail `json.loads` → nothing is returned → "Empty SSE response from upstream tools/call". The result
   is dropped even though it was fully present.

### Byte-level truth (pre-fix, probed on the real `internal_tools_call` path)

- `[notif-first]` (a `notifications/progress` frame before the result) → `result returned: False, returns notification: True`.
- `[multi-line]` (result split across two `data:` lines) → `result returned: False, Empty SSE: True`.

## The fix (CHG-0123)

The branch now parses PER EVENT (`split("\n\n")`), reassembles each event's `data:` fields joined by `"\n"`
(SSE spec, matching the org reframe CHG-0093), prefers the event carrying `result`/`error` (falls back to the
first parseable event), and skips notification events. The chosen result/error is floor-scanned via
`_scan_internal_result` before egress. A notification event is never returned — it is not the tool response, so
it never reaches the chat pipeline.

### Byte-level truth (post-fix)

- `[notif-first]` → `result returned: True, notif NOT returned: True` (the RESULT event is returned; the leading notification is skipped).
- `[multi-line]` → `result returned: True` (multi-line `data:` reassembled, not dropped).
- `[secret in result]` → `masked: True` (`AKIAIOSFODNN7EXAMPLE` in the returned result is still floor-scanned/masked).
- `[benign single]` → `result returned: True`.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_internal_http_result_scan.py -q   # 8 passed
```

New tests (drive the real `internal_tools_call` streamable-http path via an SSE-response `_fake_client`):
`test_sse_returns_result_not_leading_notification`, `test_sse_reassembles_multiline_data_result`,
`test_sse_result_secret_still_masked`.

The staged blob (HEAD + only this hunk, WITHOUT the mcp-page session's CLEANUP-06) `py_compile`s clean and is
self-coherent.

## Shared-worktree coordination (why this commit is surgical)

`mcp_proxy.py` was jointly modified: this CHG-0123 hunk (internal SSE, ~L2977) was intermixed with the
mcp-page session's **in-flight CLEANUP-06** (SSRF error sanitization in `ext_mcp_proxy` /
`internal_discover_tools` / `internal_tools_call` — routing the raw `_reason`, which can carry a DNS errno or
a resolved internal IP like `169.254.169.254`, through `sanitize_mcp_error`). That session's own AGENTS.md
note records it **deferred its commit** because `git add -p` is blocked and my SSE hunk shared the file.

Resolution: only the CHG-0123 hunk was staged, via `git apply --cached` of the isolated hunk (which bypasses
the blocked interactive `git add -p`). The mcp-page session's CLEANUP-06 SSRF hunks + its 2 test edits remain
**uncommitted in the working tree** — neither committed nor destroyed. Committing only my hunk lets their file
"settle" and **unblocks their deferred commit**.

The 2 `test_mcp_bare_proxy_scan.py::test_ext_proxy_ssrf_guard_*` failures seen in a full-suite run are that
CLEANUP-06 change (the tests assert the OLD raw-SSRF-reason contract, now sanitized). They **pass on clean
HEAD** and are independent of CHG-0123 (a different function, `ext_mcp_proxy`) — left for the mcp-page session
to reconcile with its own test updates.

## Scope / honesty note

This is a correctness/robustness parity fix on a legacy path (`MCP_HTTP_VIA_SANDBOX=0`), not a leak closure —
the path already fails safe (returned frame floor-scanned; split secret → empty). It does not change the
host-blocked live-stress status (items 14–19). Partial coverage is not completion.
