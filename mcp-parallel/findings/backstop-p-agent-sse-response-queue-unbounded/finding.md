# CHG-0129 — in-sandbox SSE response queue was unbounded (unsolicited-event flood OOM)

**Change-id:** CHG-0129
**Date:** 2026-07-03
**Severity:** MEDIUM (resource-bomb containment — closes residual #2 documented in CHG-0128). A malicious/broken upstream MCP server flooding UNSOLICITED `message` events could grow the per-session response queue without bound and OOM the sandbox agent, crashing all of that org's servers in the shared sandbox.
**Area:** HARDEN THE ARCHITECTURE — CPU/mem limits + resource-bomb containment; bound untrusted-upstream buffers (completes CHG-0128; parity with CHG-0066/0117).
**Files:** `services/mcp-broker/sandbox-image/agent/sse_manager.py` (`_new_sse_queue`, `_bounded_put`, both queue creations + the producer); `services/mcp-broker/sandbox-image/agent/tests/test_sse_reader_bounds.py` (+2 tests).
**Whose work it touches:** the owning-session in-sandbox agent SSE upstream reader (follows CHG-0128).

## Root cause

The per-session `session.sse_responses` was an **unbounded** `asyncio.Queue()`. The SSE reader
(`_sse_reader_loop`) is a **persistent background task** that keeps running between RPCs, and it `put()`s every
upstream `message`-event JSON-RPC object onto that queue. The consumer (`send_sse_jsonrpc`) only drains the
queue **while an RPC is in flight** (it `get()`s until it finds the response matching its request id, discarding
non-matching ones). So an untrusted upstream that streams **unsolicited `message` events** (not matching any
pending request) while **no RPC is active** — nobody drains — grows the queue without bound until the sandbox
agent hits its cgroup `mem_limit` and is OOM-killed. This is residual #2 called out in CHG-0128.

## The fix (CHG-0129)

- `_new_sse_queue()` creates the queue with `maxsize=_SSE_QUEUE_MAXSIZE` (default 1024, floor 16, env
  `MCP_AGENT_SSE_QUEUE_MAXSIZE`). Both creation sites (`_sse_reader_loop`, `ensure_sse_reader`) use it.
- `_bounded_put(queue, item, server_slug)` replaces the producer's `await queue.put(...)`: it `put_nowait`s and,
  on `QueueFull`, **evicts the oldest** entry (`get_nowait`) then puts the new one — atomic (no `await` between,
  so it can't interleave). This keeps the **freshest** responses (most likely to match a pending/future request)
  and, crucially, **never blocks the reader** (a blocking `put` would stall SSE parsing under backpressure).
- A waiting consumer `get()` receives the item directly and never counts against `maxsize`, so **normal RPC
  delivery is unchanged** — the bound only engages when responses pile up with no consumer (the flood case).

## Verification

```
cd services/mcp-broker/sandbox-image/agent
../../.venv/bin/python -m pytest tests/test_sse_reader_bounds.py -q     # 5 passed (3 CHG-0128 + 2 CHG-0129)
../../.venv/bin/python -m pytest tests/test_upstream_proxy.py -q        # SSE RPC path unbroken
../../.venv/bin/python -m pytest tests -q                               # full agent suite green
```

New tests: `_bounded_put` drops the oldest when full (queue stays at maxsize, retains newest); a 50-event
**unsolicited flood** with no consumer keeps `qsize() <= maxsize` and retains the freshest ids (49 present, 0
evicted).

## Scope / honesty note

Together with CHG-0128 this bounds the per-event data accumulation AND the response-queue growth on the
untrusted SSE path. The remaining residual from CHG-0128 — a single huge **unterminated** line buffering inside
httpx `aiter_lines()` before it is yielded — is still open (needs replacing `aiter_lines()` with an
`aiter_bytes()` bounded line-splitter) and is tracked separately. Does not change the host-blocked live-stress
status (items 14–19). Partial coverage is not completion.
