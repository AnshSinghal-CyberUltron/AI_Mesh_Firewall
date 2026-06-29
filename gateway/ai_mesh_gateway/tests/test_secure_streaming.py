"""Unit tests for SecureStreamingResponse buffering and terminal semantics."""
from __future__ import annotations

import json

import pytest

from ai_mesh_gateway.secure_streaming import FlushReason, SecureStreamingResponse


class _FakeScanner:
    def redact_pii(self, text: str) -> str:
        return text.replace("secret", "<REDACTED>")

    async def scan_output(self, text: str):
        class V:
            threat_type = ""
            matched_patterns = []

        return V()


class _BlockGuard:
    async def inspect(self, text: str):
        class V:
            action = "block"
            threat_type = "pii"
            detail = "detected"
            compliance_tags = ["GDPR-PII"]
            matched_patterns = ["EMAIL"]

        return V()


@pytest.mark.asyncio
async def test_secure_stream_block_emits_done():
    async def inner():
        yield 'data: {"choices":[{"delta":{"content":"secret value"}}]}\n\n'
        yield "data: [DONE]\n\n"

    secure = SecureStreamingResponse(
        inner_generator=inner(),
        scanner=_FakeScanner(),
        buffer_max_bytes=32,
        max_buffer_chunks=8,
        output_guard=_BlockGuard(),
    )
    chunks = []
    async for c in secure:
        chunks.append(c)
    assert any("[DONE]" in c for c in chunks)
    assert any("output_blocked" in c for c in chunks)


@pytest.mark.asyncio
async def test_secure_stream_block_mid_stream_after_prior_chunks_were_yielded():
    """Fail-closed contract (boundary-split-PII hardening, STREAM_LOOKAHEAD_BYTES):
    when a block fires, the buffered/held content of the blocked response is
    DROPPED — the client receives only the block frame + [DONE], never a partial
    prefix of a response that was ultimately flagged unsafe. (Previously the safe
    prefix was streamed incrementally, which let a PII token split across a chunk
    boundary leak before the next scan; the lookahead now holds a trailing window
    and releases nothing of a blocked response.)"""

    class _BlockOnSecondInspect:
        def __init__(self):
            self._calls = 0

        async def inspect(self, text: str):
            self._calls += 1
            class V:
                action = "allow"
                threat_type = ""
                detail = ""
                compliance_tags = []
                matched_patterns = []

            if self._calls >= 2 or "bad" in text:
                V.action = "block"
                V.threat_type = "pii"
                V.detail = "late block"
            return V()

    async def inner():
        yield 'data: {"choices":[{"delta":{"content":"Hi. "}}]}\n\n'
        yield 'data: {"choices":[{"delta":{"content":"bad"}}]}\n\n'
        yield "data: [DONE]\n\n"

    secure = SecureStreamingResponse(
        inner_generator=inner(),
        scanner=_FakeScanner(),
        buffer_max_bytes=4096,
        max_buffer_chunks=8,
        output_guard=_BlockOnSecondInspect(),
    )
    chunks = []
    async for c in secure:
        chunks.append(c)
    body = "".join(chunks)
    # Fail-closed: the buffered prefix of a blocked response is NOT delivered.
    assert "Hi. " not in body
    assert "output_blocked" in body
    assert any("[DONE]" in c for c in chunks)


@pytest.mark.asyncio
async def test_flag_verdict_blocks_when_enforcement_mode_block():
    class _FlagGuard:
        async def inspect(self, text: str):
            class V:
                action = "flag"
                threat_type = "pii"
                detail = "flagged"
                compliance_tags = []
                matched_patterns = ["EMAIL"]

            return V()

    async def inner():
        yield 'data: {"choices":[{"delta":{"content":"contact me"}}]}\n\n'
        yield "data: [DONE]\n\n"

    secure = SecureStreamingResponse(
        inner_generator=inner(),
        scanner=_FakeScanner(),
        buffer_max_bytes=32,
        max_buffer_chunks=8,
        output_guard=_FlagGuard(),
        enforcement_mode="block",
    )
    chunks = []
    async for c in secure:
        chunks.append(c)
    body = "".join(chunks)
    assert "output_blocked" in body
    assert any("[DONE]" in c for c in chunks)


@pytest.mark.asyncio
async def test_secure_stream_respects_max_buffer_chunks():
    calls = {"flush": 0}
    original_flush = SecureStreamingResponse._flush_buffer

    async def counting_flush(self, reason: FlushReason):
        calls["flush"] += 1
        async for x in original_flush(self, reason):
            yield x

    SecureStreamingResponse._flush_buffer = counting_flush

    async def inner():
        for i in range(20):
            yield f'data: {json.dumps({"choices":[{"delta":{"content":"x"}}]})}\n\n'
        yield "data: [DONE]\n\n"

    try:
        secure = SecureStreamingResponse(
            inner_generator=inner(),
            scanner=_FakeScanner(),
            buffer_max_bytes=10000,
            max_buffer_chunks=3,
        )
        async for _ in secure:
            pass
        assert calls["flush"] >= 2
    finally:
        SecureStreamingResponse._flush_buffer = original_flush
