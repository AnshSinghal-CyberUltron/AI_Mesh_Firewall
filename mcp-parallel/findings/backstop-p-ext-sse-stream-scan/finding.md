# CHG-0098 — ext-proxy non-finite SSE stream (server notifications) egressed UNSCANNED

**Change-id:** CHG-0098
**Date:** 2026-07-03
**Severity:** HIGH (fail-open 1.4 data leak — an entire class of egress, server-pushed SSE, was forwarded raw).
**Area:** HARDEN 1.4 — prevent MCP data leakage on ALL egress (streaming), external passthrough.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_scan_reframe_sse_tool_result` + the ext-proxy non-finite SSE branch + `_MCP_SSE_EVENT_MAX_BYTES`); `gateway/ai_mesh_gateway/tests/test_mcp_ext_sse_stream_scan.py` (new); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (updated an obsolete passthrough test).
**Whose work it touches:** the owning-session ext passthrough (CHG-0039/0061/0064/0070/0095) + the CHG-0093 SSE reframer; updates one test another session authored (it asserted the now-removed raw-passthrough behavior).

## Root cause

`ext_mcp_proxy` buffers + scans SSE responses ONLY for finite methods (tools/call, resources/*, prompts/*, initialize, `_EXT_FINITE_RESULT_METHODS`). For a NON-finite stream — server-pushed `notifications/*`, subscriptions, long-lived streams — it forwarded the SSE through RAW (`stream_gen()` yielded `resp.aiter_bytes()` verbatim), with only a `LOG.warning("streaming_egress_unscanned")`. The stated reason was that buffering an open stream could hang/OOM. But an untrusted upstream can push sensitive data in a `notifications/message` frame's `params` (or any server message), so the raw passthrough was a real, unbounded egress leak — the last unscanned MCP egress channel.

## The fix (CHG-0098)

Scan the stream **per EVENT** with bounded memory:
- `_scan_reframe_sse_tool_result` gained `scan_notifications: bool` — when set, a frame with no `result`/`error` but a `params` field (a notification) has its WHOLE JSON-RPC message scanned via the result floor (reusing the CHG-0093 per-event multi-line `data:` reassembly).
- The ext-proxy non-finite branch now buffers only up to ONE SSE event (delimited by a blank line), scans it (`scan_notifications=True`), and re-emits — so memory is bounded to one event, never the whole stream. An event exceeding `_MCP_SSE_EVENT_MAX_BYTES` (default 1 MB, env-overridable) without a boundary is WITHHELD (fail-closed) — an untrusted upstream can't force unbounded buffering by never closing an event. A blocked (unmaskable-survivor) event is withheld with an SSE comment; the stream continues. The stream is audited (`sse_stream_scanned`; withholds audited as `sse_stream_event_withheld` / `_too_large`).

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_ext_sse_stream_scan.py -q   # 5 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                    # 1581 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"    # 108 passed
```

5 new tests drive the REAL `ext_mcp_proxy` streaming path: a secret/PII/IP notification → masked in the stream (`sse_stream_scanned` audited); benign notifications pass through; a multi-line-`data:`-split notification is reassembled + masked (CHG-0093 × notification); an unmaskable-survivor notification → event withheld (fail-closed, audited); an oversized unterminated event → withheld (bounded memory, audited). The obsolete `test_ext_non_toolscall_sse_passthrough` (asserted raw passthrough) was replaced by `test_ext_non_toolscall_sse_stream_scanned`.

### Byte-level truth (streamed egress)

Notification `params.data = "key AKIAIOSFODNN7EXAMPLE email bob@corp.example ip 10.9.8.7"`:
- **FIXED** stream: `"key AKIA****MPLE and email b***@c***.example"` (secret + PII + IP masked)
- benign `notifications/progress {"progress":42}` → unchanged.

### Independent oracle (aidefence)

`aidefence_has_pii` on the streamed notification JSON: **false** on the fixed (masked) frame, **true** on the raw (pre-fix passthrough) frame.

## Scope / honesty note

Closes the last unscanned MCP egress channel (non-finite SSE) with per-event scanning and bounded memory — the original hang/OOM concern is resolved by the per-event cap, not by leaving the stream unscanned. Cross-EVENT secret splits do not reassemble client-side (SSE dispatches each event's data separately), so per-event scanning is sufficient. Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
