"""The output guard must not run once per token — task 1C.

MEASURED BUG (docs/perf/evidence/2026-09-09-flush-per-token-bug.md):

`_release_with_lookahead_tail` retains a trailing `STREAM_LOOKAHEAD_BYTES` (512)
window. For token-sized deltas (~6 bytes) that tail is 75-88 chunks — larger than
`max_buffer_chunks` (64). So once the chunk queue first fills, the retained tail
alone holds it at or above the limit permanently, and the

    if len(self._chunk_queue) >= self._max_buffer_chunks:      # secure_streaming.py:285

trigger fires on EVERY subsequent chunk. Observed end-to-end: 237 guard passes
for a 300-token answer (flushes ≈ n − 63), ~3.2 ms each ⇒ 793 ms of CPU per
request.

A byte-denominated retention and a chunk-denominated flush trigger, chosen
independently, conflict whenever deltas are small.

These tests pin the invariant: flush count must scale with CONTENT VOLUME, not
with CHUNK COUNT.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_mesh_gateway.secure_streaming import (  # noqa: E402
    DEFAULT_MAX_BUFFER_CHUNKS,
    STREAM_LOOKAHEAD_BYTES,
    SecureStreamingResponse,
)


def _sse(content: str) -> str:
    return "data: " + json.dumps({
        "id": "chatcmpl-flushtest", "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }) + "\n\n"


async def _inner(pieces):
    for p in pieces:
        yield _sse(p)
    yield "data: [DONE]\n\n"


class CountingGuard:
    """Counts inspect() calls and always allows, so only flush cadence is measured."""

    def __init__(self):
        self.calls = 0
        self.scanned_lengths: list[int] = []

    async def inspect(self, text, **_kw):
        self.calls += 1
        self.scanned_lengths.append(len(text))
        return _Allow()


class _Allow:
    action = "allow"
    threat_type = "none"
    matched_patterns: list = []
    detail = ""
    compliance_tags: list = []
    matched_values: dict = {}
    scan_degraded = False


async def _drain(stream) -> int:
    n = 0
    async for _ in stream.__aiter__():
        n += 1
    return n


@pytest.mark.asyncio
async def test_guard_does_not_run_once_per_token():
    """200 small tokens must NOT produce ~200 guard passes.

    Total content is ~1.2 KB. With a 4,096-byte buffer limit and no sentence
    boundaries, a correct implementation flushes only a handful of times.
    """
    tokens = [f"tok{i} " for i in range(200)]
    guard = CountingGuard()
    stream = SecureStreamingResponse(
        _inner(tokens), None, redaction_enabled=True, output_guard=guard,
    )
    await _drain(stream)

    total_bytes = sum(len(t.encode()) for t in tokens)
    # Generous bound: even flushing every 32 chunks would be 7. The bug produces
    # ~137. Anything approaching the token count is the death spiral.
    assert guard.calls <= len(tokens) // 8, (
        f"guard ran {guard.calls} times for {len(tokens)} tokens "
        f"({total_bytes} bytes total). The flush trigger is firing per-chunk: "
        f"the retained {STREAM_LOOKAHEAD_BYTES}-byte lookahead is more chunks "
        f"than max_buffer_chunks={DEFAULT_MAX_BUFFER_CHUNKS}, so the queue never "
        f"drops below the limit."
    )


@pytest.mark.asyncio
async def test_flush_count_scales_with_bytes_not_chunk_count():
    """Same byte volume delivered as many small chunks vs few large ones must cost
    a comparable number of guard passes.

    This is the invariant the bug violates: identical content, identical scanning
    work required, but 10x the guard passes purely because the provider chose
    smaller deltas.
    """
    text = "".join(f"tok{i} " for i in range(200))

    small = [text[i:i + 6] for i in range(0, len(text), 6)]     # many tiny chunks
    large = [text[i:i + 120] for i in range(0, len(text), 120)]  # few large chunks

    g_small, g_large = CountingGuard(), CountingGuard()
    await _drain(SecureStreamingResponse(
        _inner(small), None, redaction_enabled=True, output_guard=g_small))
    await _drain(SecureStreamingResponse(
        _inner(large), None, redaction_enabled=True, output_guard=g_large))

    assert g_small.calls <= max(3 * g_large.calls, g_large.calls + 3), (
        f"same {len(text)} bytes cost {g_small.calls} guard passes as 6-byte chunks "
        f"but only {g_large.calls} as 120-byte chunks. Flush cadence is driven by "
        f"chunk COUNT rather than content volume."
    )


@pytest.mark.asyncio
async def test_scanned_window_stays_bounded():
    """Retention must keep the scan window bounded — this property already held and
    must not regress when the flush trigger changes."""
    tokens = [f"tok{i} " for i in range(300)]
    guard = CountingGuard()
    await _drain(SecureStreamingResponse(
        _inner(tokens), None, redaction_enabled=True, output_guard=guard))

    assert guard.scanned_lengths, "guard never ran"
    worst = max(guard.scanned_lengths)
    assert worst <= 4 * STREAM_LOOKAHEAD_BYTES, (
        f"scan window grew to {worst} chars; retention should bound it near "
        f"{STREAM_LOOKAHEAD_BYTES}"
    )
