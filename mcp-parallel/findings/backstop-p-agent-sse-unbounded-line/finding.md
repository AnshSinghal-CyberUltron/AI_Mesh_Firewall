# CHG-0130 — SSE readers buffered a single unterminated line with no cap (httpx aiter_lines OOM)

**Change-id:** CHG-0130
**Date:** 2026-07-03
**Severity:** MEDIUM (resource-bomb containment — closes the last residual from CHG-0128). A malicious/broken upstream MCP server sending one enormous line with NO newline could OOM the sandbox agent (crashing all of that org's servers in the shared sandbox) BEFORE the CHG-0128 per-event cap or CHG-0129 queue bound could act.
**Area:** HARDEN THE ARCHITECTURE — resource-bomb containment; bound untrusted-upstream reads (completes the CHG-0128/0129 SSE-reader triad; parity CHG-0066/0117).
**Files:** `services/mcp-broker/sandbox-image/agent/upstream_manager.py` (new `_aiter_sse_lines_bounded`; `_post_streamable_http` SSE branch); `services/mcp-broker/sandbox-image/agent/sse_manager.py` (`_sse_reader_loop` uses it); tests `test_sse_reader_bounds.py` (+1), `test_upstream_proxy.py` and `test_rpc_unified.py` (SSE fakes migrated to yield bytes via `aiter_bytes`).
**Whose work it touches:** the owning-session in-sandbox agent SSE read paths (both the legacy sse-transport reader and the streamable-http SSE branch).

## Root cause

Both SSE read paths iterated `response.aiter_lines()`:

- `sse_manager._sse_reader_loop` (legacy HTTP+SSE transport, long-lived GET /sse), and
- `upstream_manager._post_streamable_http` (the streamable-http POST whose reply is `text/event-stream`).

httpx's `aiter_lines()` accumulates bytes into an internal `LineDecoder` buffer until it finds a line boundary,
with **no size cap**. So an untrusted upstream that sends one enormous line **with no newline** grows that buffer
until the sandbox agent hits its cgroup `mem_limit` and is OOM-killed — and this happens **inside `aiter_lines`,
before the line is ever yielded**, so the CHG-0128 per-event `data:` cap and the CHG-0129 queue bound (both of
which act only *after* a line is yielded) cannot help. This is the residual #1 documented in CHG-0128/0129.
`_post_streamable_http` already capped multi-line accumulation (its `total` counter) but shared the same
single-line `aiter_lines` weakness.

## The fix (CHG-0130)

A shared `_aiter_sse_lines_bounded(response, max_line_bytes)` reads raw bytes via `aiter_bytes()`, splits on
`\n` (stripping a trailing `\r`), and yields decoded lines — **raising `UpstreamError("upstream SSE line too
large")` as soon as an UNTERMINATED line buffer would exceed `max_line_bytes` (`_MAX_RESPONSE_BYTES`, 8 MB)**.
The in-memory buffer is therefore bounded to ≈ one line's cap. Both read paths now use it:

- `_sse_reader_loop` — the reader task ends on the oversized line (its `except UpstreamError: raise`); the next
  RPC restarts a fresh reader (`ensure_sse_reader` sees the task done).
- `_post_streamable_http` — the oversized line surfaces as an upstream error for that call.

Well-formed streams (single-line and legitimate multi-line events) are parsed exactly as before.

## Verification

```
cd services/mcp-broker/sandbox-image/agent
../../.venv/bin/python -m pytest tests/test_sse_reader_bounds.py -q     # 6 passed
../../.venv/bin/python -m pytest tests/test_upstream_proxy.py -q        # both SSE paths green (fakes now yield bytes)
../../.venv/bin/python -m pytest tests -q                              # full agent suite green
```

New `test_single_unterminated_huge_line_is_bounded`: one giant line with no newline, streamed in chunks, with a
small cap → the reader raises `"line too large"` (buffer bounded) rather than OOM. The prior CHG-0128/0129 tests
(per-event cap, queue bound, normal + multi-line delivery) still pass with the fakes migrated to `aiter_bytes`.

Note: the SSE fakes in `test_upstream_proxy.py` and `test_rpc_unified.py` were migrated to serve their frames via
`aiter_bytes()` (a delegating generator over their existing `aiter_lines()`), since the reader now consumes bytes.
`test_rpc_unified.py::test_all_transports_accepted_on_post_rpc[sse]` caught the one fake initially missed — fixed
and re-verified green (7 passed).

## Scope / honesty note

This closes the last of the three untrusted-SSE buffering vectors (per-event data — CHG-0128; response queue —
CHG-0129; single unterminated line — CHG-0130). The line splitter matches on `\n`/`\r\n` (SSE-standard); a lone
`\r`-only terminator (non-standard, not used by MCP servers) is not treated as a boundary — negligible. Does not
change the host-blocked live-stress status (items 14–19). Partial coverage is not completion.
