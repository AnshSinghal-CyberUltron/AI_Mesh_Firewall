"""M-51: terminal SSE zeroshield trace frame on every streaming termination path.

Each terminating stream must emit EXACTLY ONE ChatCompletionChunk-shaped frame
with ``choices: []`` and a top-level ``zeroshield`` object immediately before
``data: [DONE]`` — on allow, redact, flag, block, upstream-error and
exception terminations alike.
"""
from __future__ import annotations

import json

import pytest

from ai_mesh_gateway.secure_streaming import SecureStreamingResponse
from ai_mesh_gateway.stream_orchestration import (
    StreamFinalizeHooks,
    StreamLaunchContext,
    StreamRunMetrics,
    build_stream_trace_frame,
    stream_with_finalize,
)


def _ctx(**overrides) -> StreamLaunchContext:
    defaults = dict(
        body={"model": "gpt-4o-mini"},
        redacted_prompt=None,
        org_slug="acme",
        model="gpt-4o-mini",
        request_id="zs-stream-test001",
    )
    defaults.update(overrides)
    return StreamLaunchContext(**defaults)


def _parse_sse(chunk: str):
    line = chunk.strip()
    if not line.startswith("data: ") or line == "data: [DONE]":
        return None
    try:
        return json.loads(line[6:])
    except (json.JSONDecodeError, TypeError):
        return None


def _trace_frames(chunks: list[str]) -> list[dict]:
    frames = []
    for c in chunks:
        data = _parse_sse(c)
        if isinstance(data, dict) and "zeroshield" in data:
            frames.append(data)
    return frames


async def _collect(gen) -> list[str]:
    out = []
    async for chunk in gen:
        out.append(chunk)
    return out


class _FakeScanner:
    def redact_pii(self, text: str) -> str:
        return text.replace("secret", "<REDACTED>")

    async def scan_output(self, text: str):
        class V:
            threat_type = ""
            matched_patterns = []

        return V()


class _PiiScanner(_FakeScanner):
    async def scan_output(self, text: str):
        class V:
            threat_type = "pii"
            matched_patterns = ["EMAIL"]
            detail = "email found"
            confidence = 0.9

        return V()


def _guard(action: str):
    class _Guard:
        async def inspect(self, text: str, **_kw):
            class V:
                pass

            V.action = action
            V.threat_type = "pii"
            V.detail = f"guard {action}"
            V.compliance_tags = []
            V.matched_patterns = ["EMAIL"]
            return V()

    return _Guard()


# ──────────────────────────────────────────────────────────────────────────
# Choke point: stream_with_finalize
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allow_path_emits_exactly_one_trace_before_done():
    async def inner():
        yield 'data: {"id":"chatcmpl-abc","model":"gpt-4o-mini","choices":[{"delta":{"content":"Hi"}}]}\n\n'
        yield "data: [DONE]\n\n"

    chunks = await _collect(
        stream_with_finalize(inner(), _ctx(), StreamFinalizeHooks(), zeroshield_base={"action": "allow"})
    )
    traces = _trace_frames(chunks)
    assert len(traces) == 1
    frame = traces[0]
    # ChatCompletionChunk shape: SDK-parseable with empty choices
    assert frame["object"] == "chat.completion.chunk"
    assert frame["choices"] == []
    assert isinstance(frame["created"], int)
    # id/model copied from the upstream stream
    assert frame["id"] == "chatcmpl-abc"
    assert frame["model"] == "gpt-4o-mini"
    assert frame["zeroshield"]["action"] == "allow"
    assert frame["zeroshield"]["request_id"] == "zs-stream-test001"
    # Trace is the second-to-last chunk; [DONE] is last
    assert chunks[-1].strip() == "data: [DONE]"
    assert _parse_sse(chunks[-2]) == frame


@pytest.mark.asyncio
async def test_legacy_callers_without_base_get_no_trace_frame():
    async def inner():
        yield 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        yield "data: [DONE]\n\n"

    chunks = await _collect(stream_with_finalize(inner(), _ctx(), StreamFinalizeHooks()))
    assert _trace_frames(chunks) == []
    assert len(chunks) == 2


@pytest.mark.asyncio
async def test_stream_without_done_sentinel_gets_trace_and_done():
    async def inner():
        yield 'data: {"id":"chatcmpl-x","choices":[{"delta":{"content":"Hi"}}]}\n\n'
        # ends without [DONE]

    chunks = await _collect(
        stream_with_finalize(inner(), _ctx(), StreamFinalizeHooks(), zeroshield_base={"action": "allow"})
    )
    traces = _trace_frames(chunks)
    assert len(traces) == 1
    assert chunks[-1].strip() == "data: [DONE]"
    assert _parse_sse(chunks[-2]) == traces[0]


@pytest.mark.asyncio
async def test_upstream_error_frame_yields_error_trace():
    async def inner():
        yield 'data: {"error": {"message": "model exploded", "type": "upstream_error"}}\n\n'
        yield "data: [DONE]\n\n"

    chunks = await _collect(
        stream_with_finalize(inner(), _ctx(), StreamFinalizeHooks(), zeroshield_base={"action": "allow"})
    )
    traces = _trace_frames(chunks)
    assert len(traces) == 1
    assert traces[0]["zeroshield"]["action"] == "error"
    assert chunks[-1].strip() == "data: [DONE]"


@pytest.mark.asyncio
async def test_exception_mid_stream_emits_trace_and_done_then_raises():
    async def failing():
        yield 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        raise RuntimeError("boom")

    chunks = []
    gen = stream_with_finalize(failing(), _ctx(), StreamFinalizeHooks(), zeroshield_base={"action": "allow"})
    with pytest.raises(RuntimeError):
        async for c in gen:
            chunks.append(c)
    traces = _trace_frames(chunks)
    assert len(traces) == 1
    assert traces[0]["zeroshield"]["action"] == "error"
    assert chunks[-1].strip() == "data: [DONE]"


@pytest.mark.asyncio
async def test_trace_includes_usage_when_known():
    metrics = StreamRunMetrics(usage={"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12})

    async def inner():
        yield "data: [DONE]\n\n"

    chunks = await _collect(
        stream_with_finalize(
            inner(), _ctx(), StreamFinalizeHooks(), metrics=metrics, zeroshield_base={"action": "allow"}
        )
    )
    traces = _trace_frames(chunks)
    assert len(traces) == 1
    assert traces[0]["usage"]["total_tokens"] == 12


@pytest.mark.asyncio
async def test_double_done_emits_single_trace():
    async def inner():
        yield 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        yield "data: [DONE]\n\n"
        yield "data: [DONE]\n\n"

    chunks = await _collect(
        stream_with_finalize(inner(), _ctx(), StreamFinalizeHooks(), zeroshield_base={"action": "allow"})
    )
    assert len(_trace_frames(chunks)) == 1


# ──────────────────────────────────────────────────────────────────────────
# Full pipeline: SecureStreamingResponse → stream_with_finalize
# ──────────────────────────────────────────────────────────────────────────


def _secure(inner, *, scanner=None, guard=None, metrics=None, enforcement="block"):
    return SecureStreamingResponse(
        inner_generator=inner,
        scanner=scanner or _FakeScanner(),
        buffer_max_bytes=4096,
        max_buffer_chunks=8,
        output_guard=guard,
        stream_metrics=metrics,
        enforcement_mode=enforcement,
    ).__aiter__()


@pytest.mark.asyncio
async def test_block_termination_emits_block_trace_once():
    async def inner():
        yield 'data: {"id":"chatcmpl-blk","choices":[{"delta":{"content":"leak. "}}]}\n\n'
        yield "data: [DONE]\n\n"

    metrics = StreamRunMetrics()
    secure = _secure(inner(), guard=_guard("block"), metrics=metrics)
    chunks = await _collect(
        stream_with_finalize(
            secure, _ctx(), StreamFinalizeHooks(), metrics=metrics, zeroshield_base={"action": "allow"}
        )
    )
    traces = _trace_frames(chunks)
    assert len(traces) == 1
    zs = traces[0]["zeroshield"]
    assert zs["action"] == "block"
    assert zs["detection_tier"] == "output_guard"
    assert zs["threat_type"] == "pii"
    assert zs["matched_patterns"] == ["EMAIL"]
    # Sequence: error frame (output_blocked), then trace, then [DONE] last
    assert any("output_blocked" in c for c in chunks)
    assert chunks[-1].strip() == "data: [DONE]"
    assert _parse_sse(chunks[-2]) == traces[0]


@pytest.mark.asyncio
async def test_flag_under_block_enforcement_traces_flag():
    """F-003: flag stays flag under enforcement_mode=block (UI honesty)."""
    async def inner():
        yield 'data: {"choices":[{"delta":{"content":"hello. "}}]}\n\n'
        yield "data: [DONE]\n\n"

    metrics = StreamRunMetrics()
    secure = _secure(inner(), guard=_guard("flag"), metrics=metrics, enforcement="block")
    chunks = await _collect(
        stream_with_finalize(
            secure, _ctx(), StreamFinalizeHooks(), metrics=metrics, zeroshield_base={"action": "allow"}
        )
    )
    traces = _trace_frames(chunks)
    assert len(traces) == 1
    assert traces[0]["zeroshield"]["action"] == "flag"
    assert not any("output_blocked" in c for c in chunks)


@pytest.mark.asyncio
async def test_guard_redact_termination_traces_redact():
    async def inner():
        yield 'data: {"choices":[{"delta":{"content":"my secret. "}}]}\n\n'
        yield "data: [DONE]\n\n"

    metrics = StreamRunMetrics()
    secure = _secure(inner(), guard=_guard("redact"), metrics=metrics)
    chunks = await _collect(
        stream_with_finalize(
            secure, _ctx(), StreamFinalizeHooks(), metrics=metrics, zeroshield_base={"action": "allow"}
        )
    )
    traces = _trace_frames(chunks)
    assert len(traces) == 1
    zs = traces[0]["zeroshield"]
    assert zs["action"] == "redact"
    assert zs["detection_tier"] == "output_guard"
    # Redacted content still flows, then trace, then [DONE]
    assert any("<REDACTED>" in c for c in chunks)
    assert chunks[-1].strip() == "data: [DONE]"


@pytest.mark.asyncio
async def test_scanner_only_pii_redaction_traces_redact():
    async def inner():
        yield 'data: {"choices":[{"delta":{"content":"mail me secret. "}}]}\n\n'
        yield "data: [DONE]\n\n"

    metrics = StreamRunMetrics()
    secure = _secure(inner(), scanner=_PiiScanner(), guard=None, metrics=metrics)
    chunks = await _collect(
        stream_with_finalize(
            secure, _ctx(), StreamFinalizeHooks(), metrics=metrics, zeroshield_base={"action": "allow"}
        )
    )
    traces = _trace_frames(chunks)
    assert len(traces) == 1
    assert traces[0]["zeroshield"]["action"] == "redact"
    assert traces[0]["zeroshield"]["matched_patterns"] == ["EMAIL"]


@pytest.mark.asyncio
async def test_guard_flag_monitor_mode_traces_flag():
    async def inner():
        yield 'data: {"choices":[{"delta":{"content":"hello. "}}]}\n\n'
        yield "data: [DONE]\n\n"

    metrics = StreamRunMetrics()
    secure = _secure(inner(), guard=_guard("flag"), metrics=metrics, enforcement="monitor")
    chunks = await _collect(
        stream_with_finalize(
            secure, _ctx(), StreamFinalizeHooks(), metrics=metrics, zeroshield_base={"action": "allow"}
        )
    )
    traces = _trace_frames(chunks)
    assert len(traces) == 1
    assert traces[0]["zeroshield"]["action"] == "flag"


# ──────────────────────────────────────────────────────────────────────────
# Builders
# ──────────────────────────────────────────────────────────────────────────


def test_build_stream_trace_frame_block_overlay():
    metrics = StreamRunMetrics(output_blocked=True)
    metrics.record_guard_action("block", threat_type="secret", detail="api key", matched_patterns=["AWS_KEY"])
    frame = json.loads(build_stream_trace_frame(_ctx(), metrics, {"action": "allow"}).strip()[6:])
    zs = frame["zeroshield"]
    assert zs["action"] == "block"
    assert zs["threat_type"] == "secret"
    assert zs["matched_patterns"] == ["AWS_KEY"]
    assert frame["choices"] == []


def test_record_guard_action_keeps_strongest():
    metrics = StreamRunMetrics()
    metrics.record_guard_action("flag", threat_type="pii")
    metrics.record_guard_action("redact", threat_type="pii")
    metrics.record_guard_action("flag", threat_type="other")  # weaker: ignored
    assert metrics.guard_action == "redact"
    assert metrics.guard_threat_type == "pii"


def test_build_stream_zeroshield_base_is_client_safe():
    from ai_mesh_gateway import main as gateway_main

    class Verdict:
        action = "allow"
        tier = "tier_1"
        threat_type = "none"
        confidence = 0.0
        matched_patterns = []
        detail = ""
        scan_meta = {}
        reason_code = ""

    zs = gateway_main._build_stream_zeroshield_base(
        request_id="zs-stream-xyz", scan_verdict=Verdict(), redacted_prompt=None
    )
    assert zs["request_id"] == "zs-stream-xyz"
    assert zs["action"] == "allow"
    # Internal-only fields must NOT leak to the client trace
    for forbidden in ("compliance_tags", "original_prompt_hash", "redacted_prompt", "security_incident"):
        assert forbidden not in zs


def test_build_stream_zeroshield_base_redact_attribution():
    from ai_mesh_gateway import main as gateway_main

    class Verdict:
        action = "redact"
        tier = "tier_1"
        threat_type = "pii"
        confidence = 0.95
        matched_patterns = ["SSN"]
        detail = "ssn found"

    zs = gateway_main._build_stream_zeroshield_base(
        request_id="zs-stream-red", scan_verdict=Verdict(), redacted_prompt="my ssn is [REDACTED]"
    )
    assert zs["action"] == "redact"
    assert zs["detection_tier"] == "tier_1"
    assert zs["threat_type"] == "pii"
    assert zs["matched_patterns"] == ["SSN"]
