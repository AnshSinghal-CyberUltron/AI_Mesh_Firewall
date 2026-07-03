# CHG-0128 — in-sandbox SSE reader buffered an untrusted upstream event with no size cap (OOM/mem-bomb)

**Change-id:** CHG-0128
**Date:** 2026-07-03
**Severity:** MEDIUM (resource-bomb containment — a malicious/broken upstream MCP server could OOM the sandbox agent by streaming unbounded `data:` lines in one SSE event; the crash takes down ALL of that org's MCP servers sharing the sandbox). Directly relevant to stress item 17 ("resource bombs contained; neighbors + host safe").
**Area:** HARDEN THE ARCHITECTURE — CPU/mem limits + resource-bomb containment; bound untrusted upstream reads (parity with the gateway ext-SSE bounds CHG-0117 and the streamable-http per-response cap CHG-0066).
**Files:** `services/mcp-broker/sandbox-image/agent/sse_manager.py` (`_sse_reader_loop`); `services/mcp-broker/sandbox-image/agent/tests/test_sse_reader_bounds.py` (new, +3 tests).
**Whose work it touches:** the owning-session in-sandbox agent SSE upstream reader (legacy HTTP+SSE transport).

## Root cause

`_sse_reader_loop` maintains a long-lived `GET /sse` to the **untrusted third-party MCP server** and parses
events:

```python
data_lines: list[str] = []
async for line in response.aiter_lines():
    ...
    elif line.startswith("data:"):
        data_lines.append(line[5:].strip())     # NO size cap
    elif line == "" and data_lines:
        data = "\n".join(data_lines); ...
```

`data_lines` accumulated every `data:` line of an event until a terminating blank line, with **no bound**.
A malicious or broken upstream could stream millions of `data:` lines (or very large ones) **without** sending
the blank line, growing `data_lines` until the sandbox agent hits its cgroup `mem_limit` and is OOM-killed. That
crash is contained to the sandbox (good — the host is safe), but it takes down **every** MCP server for that org
sharing the per-org sandbox — a within-tenant availability bomb. The sibling read paths were already bounded:
the streamable-http per-response read (CHG-0066), the ext-proxy SSE stream at the gateway (CHG-0117), and the WS
per-message cap — but this in-sandbox SSE reader was not.

## The fix (CHG-0128)

The reader now tracks the accumulated event size (`data_bytes`) and, when it would exceed
`_MAX_RESPONSE_BYTES` (8 MB default, env `MCP_AGENT_MAX_RESPONSE_BYTES`), **drops the oversized event** and
sets a `skipping` flag that discards the remainder of that event until its terminating blank line — then
resumes normally for the next event. Legitimate events (including multi-line `data:` under the cap) are
unchanged. The reader keeps running (no crash, no reader-death).

## Verification

```
cd services/mcp-broker/sandbox-image/agent
../../.venv/bin/python -m pytest tests/test_sse_reader_bounds.py -q     # 3 passed
../../.venv/bin/python -m pytest tests/test_upstream_proxy.py -q        # 18 passed (SSE path unbroken)
../../.venv/bin/python -m pytest tests -q                               # full agent suite green
```

New tests: a normal message event is delivered; an oversized event (data lines > cap) is **dropped** while a
**subsequent** normal event is still delivered (proves the reader survives); a legitimate multi-line `data:`
event under the cap is still joined and parsed.

## Scope / honesty note (residuals)

This bounds the dominant vector — unbounded **per-event `data:` accumulation**. Two related buffers on the same
reader remain and are noted for a follow-up:

1. **Single huge unterminated line:** `response.aiter_lines()` (httpx) buffers one line with no cap, so an
   upstream sending one enormous line with no `\n` can still buffer unboundedly *inside* `aiter_lines` before it
   is yielded. Fully closing this needs replacing `aiter_lines()` with a bounded `aiter_bytes()` line splitter
   (a larger change coupled to the test fakes) — deferred.
2. **`session.sse_responses` queue** is unbounded (`asyncio.Queue()`), so a flood of small valid `message`
   events faster than the consumer drains could grow memory; bounding it needs drop/backpressure semantics that
   affect id-matched response delivery — deferred.

Does not change the host-blocked live-stress status (items 14–19). Partial coverage is not completion.
