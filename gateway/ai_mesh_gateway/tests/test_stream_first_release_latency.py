"""How long does the CALLER wait for a first token? — task 1D.

MEASURED (docs/perf/evidence/2026-09-09-1D-streaming-head-latency.md), Docker
E2E, 100 tok/s stub:

    tokens   HEAD (to 1st token at client)
       100        3097 ms   <- the ENTIRE generation; nothing streamed at all
       200        2042 ms
       300        1393 ms

HEAD *falls* as the answer gets longer. That is the signature of a fixed CHUNK
COUNT threshold, not a time cost: a longer answer reaches the threshold sooner in
wall-clock terms because its tokens arrive faster.

Two independent hold-backs compose:

  1. `_release_with_lookahead_tail` retains `STREAM_LOOKAHEAD_BYTES` (512)
     UNCONDITIONALLY (`min_retain = STREAM_LOOKAHEAD_BYTES`), so the client is
     always >=512 bytes (~85 token-sized chunks) behind.
  2. A non-final flush only happens every `max_buffer_chunks` (64) chunks, so
     the release itself is quantised to 64-chunk steps.

Composed: the first byte reaches the client at the first multiple of 64 whose
accumulated content exceeds 512 bytes -- chunk 128 for ~6-byte tokens -- or never
before [DONE] if the answer is shorter than that.

A streaming request is therefore not, from the caller's point of view, a stream.

These tests measure the caller-visible property directly: at which INPUT chunk
index does the first byte come out.
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
        "id": "chatcmpl-headtest", "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }) + "\n\n"


class _Allow:
    action = "allow"
    threat_type = "none"
    matched_patterns: list = []
    detail = ""
    compliance_tags: list = []
    matched_values: dict = {}
    scan_degraded = False


class AllowGuard:
    async def inspect(self, text, **_kw):
        return _Allow()


async def first_release_index(tokens: list[str]) -> tuple[int | None, int]:
    """Return (input-chunk index at which the first content byte was released,
    total input chunks). None means nothing was released until [DONE]."""
    fed = 0
    first: int | None = None

    async def inner():
        nonlocal fed
        for t in tokens:
            fed += 1
            yield _sse(t)
        yield "data: [DONE]\n\n"

    stream = SecureStreamingResponse(
        inner(), None, redaction_enabled=True, output_guard=AllowGuard())
    async for frame in stream.__aiter__():
        if '"content"' in frame and first is None and fed < len(tokens):
            first = fed
    return first, len(tokens)


@pytest.mark.xfail(strict=True, reason=(
    "OPEN DEFECT (task 1E): min_retain = STREAM_LOOKAHEAD_BYTES is unconditional, so "
    "a <512-byte answer is never released before [DONE]. strict=True so this flips to "
    "XPASS and forces the marker's removal the moment 1E lands."))
@pytest.mark.asyncio
async def test_short_answer_is_not_streamed_at_all():
    """A 100-token answer is delivered entirely at [DONE].

    100 x ~6 bytes = ~600 bytes. The first non-final flush is at chunk 64
    (~384 bytes), which is under the 512-byte retention, so it releases NOTHING;
    the next would be chunk 128, which never arrives. The caller waits the whole
    generation and then receives everything at once.
    """
    first, n = await first_release_index([f"tok{i} " for i in range(100)])
    assert first is not None, (
        f"nothing reached the client before [DONE] for a {n}-token answer: the "
        f"{STREAM_LOOKAHEAD_BYTES}-byte retention exceeds the content released at "
        f"the only non-final flush (chunk {DEFAULT_MAX_BUFFER_CHUNKS}), so a short "
        f"streaming answer is delivered as one blob at the end. Caller-visible "
        f"TTFT == full generation time."
    )


@pytest.mark.xfail(strict=True, reason=(
    "OPEN DEFECT (task 1E): first release lands at input chunk 129 (measured); budget "
    "is 32. Dominated by the unconditional 512-byte retention, not by the flush "
    "trigger. strict=True so it flips to XPASS when 1E lands."))
@pytest.mark.asyncio
async def test_first_token_reaches_client_early():
    """The caller must see a first token within a bounded PREFIX of the answer.

    Streaming exists so the caller sees output while generation continues. A
    firewall that holds the first 128 chunks has converted the stream into a
    batch response for the first ~43% of a 300-token answer.
    """
    first, n = await first_release_index([f"tok{i} " for i in range(300)])
    assert first is not None and first <= 32, (
        f"first byte released only after {first} of {n} input chunks. Budget is 32. "
        f"Two hold-backs compose: an unconditional {STREAM_LOOKAHEAD_BYTES}-byte "
        f"lookahead retention AND a flush that fires only every "
        f"{DEFAULT_MAX_BUFFER_CHUNKS} chunks, so the first release lands at the "
        f"first multiple of {DEFAULT_MAX_BUFFER_CHUNKS} exceeding "
        f"{STREAM_LOOKAHEAD_BYTES} bytes."
    )
