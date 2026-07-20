# CHG-0117 — non-finite SSE stream had no TOTAL bound (infinite-stream resource bomb)

**Change-id:** CHG-0117
**Date:** 2026-07-03
**Severity:** MEDIUM (fail-open DoS — an untrusted allowlisted upstream could hold the gateway connection + burn CPU + egress unbounded data FOREVER via an infinite SSE stream).
**Area:** HARDEN THE ARCHITECTURE — resource-bomb containment (timeout / hours-long-soak), streaming-path twin of the CHG-0104/0115/0116 caps.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`ext_mcp_proxy` non-finite SSE `stream_gen` + `_MCP_SSE_STREAM_MAX_BYTES` / `_MCP_SSE_STREAM_MAX_EVENTS`); `gateway/ai_mesh_gateway/tests/test_mcp_ext_sse_stream_scan.py` (+3 tests).
**Whose work it touches:** the owning-session `ext_mcp_proxy` non-finite SSE streaming path (extends CHG-0098).

## Root cause

The ext-proxy NON-FINITE SSE `stream_gen` (CHG-0098 — server notifications / `*subscribe` / long-lived streams)
scans each event and caps each EVENT at `_MCP_SSE_EVENT_MAX_BYTES` (1MB), never buffering the whole stream
(memory-safe). But it had **no TOTAL bound** — no cap on total bytes, total events, or duration:

```python
async for chunk in resp.aiter_bytes():     # runs as long as the upstream streams
    buf += chunk...
    while "\n\n" in buf: ... scan + yield each event ...
    if len(buf) > _MCP_SSE_EVENT_MAX_BYTES: ... withhold oversized event ...
    # (no total bound)
```

An untrusted (compromised allowlisted) upstream can stream an INFINITE sequence of small (<1MB) events forever: the
loop runs indefinitely, holding the gateway connection, burning CPU scanning + re-emitting each event, and egressing
unbounded data to the client. httpx's per-read timeout only bounds the gap BETWEEN reads — a slow-but-steady infinite
stream (an event every fraction of the timeout) never trips it. A resource/time bomb ("hours-long soak" / "timeout"
in the stress mandate).

## The fix (CHG-0117)

`stream_gen` now tracks `total_bytes` and `event_count`, and once either crosses a total cap it CLOSES the stream
fail-closed:

```python
if total_bytes > _MCP_SSE_STREAM_MAX_BYTES or event_count > _MCP_SSE_STREAM_MAX_EVENTS:
    LOG.warning("ext_mcp_proxy.sse_stream_limit host=%s bytes=%d events=%d (CLOSING)", ...)
    await _ext_audit("block", "sse_stream_limit_exceeded", tool=_ext_tool_name)
    yield b": [stream closed: resource limit]\n\n"
    return                      # the `finally` still aclose()s the upstream resp + client
```

- `_MCP_SSE_STREAM_MAX_BYTES` = 100MB (env `MCP_SSE_STREAM_MAX_BYTES`).
- `_MCP_SSE_STREAM_MAX_EVENTS` = 100000 (env `MCP_SSE_STREAM_MAX_EVENTS`).

Generous defaults ≫ any realistic long-lived notification feed (which sends little data slowly), so a legit stream is
unaffected; a runaway/infinite stream is contained and its upstream connection is closed. The byte cap contains a
high-volume bomb; the event cap contains a high-frequency-small-event bomb.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_ext_sse_stream_scan.py -q   # 8 passed (5 + 3 new)
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                   # 1709 passed, 0 failed
```

3 new tests drive the REAL `ext_mcp_proxy` streaming path: with `_MCP_SSE_STREAM_MAX_EVENTS=5`, a 50-event stream
emits `: [stream closed: resource limit]` and stops early (`sse_stream_limit_exceeded` audited); with a low
`_MCP_SSE_STREAM_MAX_BYTES`, a 500B-per-event stream closes; with default caps, a 5-event stream streams all 5 and
never closes-on-limit. Broker unaffected (gateway-only change).

## Scope / honesty note

Resource-bomb containment on the one unbounded streaming path (ext-proxy non-finite SSE); the finite SSE/JSON result
paths are already buffer-bounded. **Oracle:** N/A — a DoS-containment fix, not a PII-text leak (no egress-content
delta); the stream-closes-at-cap + under-cap-completes byte assertions are authoritative. Resource-bomb containment
is now at parity across byte cap (CHG-0034/0063/0064) + block-count (CHG-0104) + depth (CHG-0115 result / CHG-0116
args) + exfil-walk (CHG-0114) + total-stream (CHG-0117). Does not change the host-blocked live-stress status (items
14–20). Partial coverage is not completion.
