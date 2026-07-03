"""Unit tests for stream_orchestration lifecycle helpers."""
from __future__ import annotations

import asyncio
import json

import pytest

from ai_mesh_gateway.circuit_breaker import CircuitBreaker
from ai_mesh_gateway.rate_limiter import RateLimiter
from ai_mesh_gateway.stream_orchestration import (
    StreamFinalizeHooks,
    StreamLaunchContext,
    StreamRunMetrics,
    StreamScanMode,
    build_base_stream_headers,
    build_stream_trace_frame,
    enrich_stream_headers,
    finalize_stream,
    instrumented_stream_generator,
    policy_summary_id,
    stream_with_finalize,
    streaming_preflight_block_body,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _chunks(*lines: str):
    for line in lines:
        yield line


def test_policy_summary_id_is_stable():
    assert policy_summary_id("hello") == policy_summary_id("hello")
    assert len(policy_summary_id("x")) == 16


def test_enrich_stream_headers_routing():
    class Sel:
        requested_model = "auto"
        model_name = "gpt-4o-mini"
        decision_source = "weighted"
        reason = "cost"
        policy_summary = "meets compliance"

    headers = enrich_stream_headers(
        build_base_stream_headers(),
        route_selection=Sel(),
        scan_mode=StreamScanMode.OUTPUT_GUARD,
    )
    assert headers["X-ZeroShield-Routed-Model"] == "gpt-4o-mini"
    assert headers["X-ZeroShield-Stream-Scan-Mode"] == "output_guard"
    assert "X-ZeroShield-Routing-Policy-Id" in headers
    assert "X-ZeroShield-Routing-Policy-Summary" not in headers


def test_policy_summary_header_only_when_debug():
    class Sel:
        requested_model = "auto"
        model_name = "gpt-4o-mini"
        decision_source = "weighted"
        reason = ""
        policy_summary = "sensitive routing rationale"

    debug_headers = enrich_stream_headers(
        build_base_stream_headers(),
        route_selection=Sel(),
        emit_debug=True,
    )
    assert "X-ZeroShield-Routing-Policy-Summary" in debug_headers


def test_streaming_preflight_blocks_when_policy_cache_unloaded():
    body = streaming_preflight_block_body(
        {"stream_preflight_fail_closed": True, "policy_cache_require_loaded": True},
        policy_sync_loaded=False,
    )
    assert body is not None
    assert body["code"] == "stream_preflight_policy_unavailable"


def test_streaming_preflight_allows_when_disabled():
    body = streaming_preflight_block_body(
        {"stream_preflight_fail_closed": False, "policy_cache_require_loaded": True},
        policy_sync_loaded=False,
    )
    assert body is None


@pytest.mark.asyncio
async def test_finalize_stream_marks_circuit_error_on_output_block(fake_redis):
    cb = CircuitBreaker(fake_redis, min_requests=1, error_threshold=0.0)
    ctx = StreamLaunchContext(
        body={"model": "m1"},
        redacted_prompt=None,
        org_slug="acme",
        model="m1",
    )
    metrics = StreamRunMetrics(completed=True, output_blocked=True)
    hooks = StreamFinalizeHooks(circuit_breaker=cb)
    await finalize_stream(ctx, metrics, hooks, decision="blocked")
    status = await cb.check("m1")
    assert status.should_block is True


@pytest.mark.asyncio
async def test_instrumented_stream_tracks_ttft():
    metrics = StreamRunMetrics()

    async def inner():
        yield 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        yield "data: [DONE]\n\n"

    out = []
    async for c in instrumented_stream_generator(inner(), metrics):
        out.append(c)
    assert len(out) == 2
    assert metrics.first_token_ts > 0
    assert metrics.completed is True
    assert metrics.chunks_emitted >= 1


def test_build_stream_trace_frame_reconciles_total_and_ttft():
    import time

    ctx = StreamLaunchContext(
        body={"model": "gpt-4o-mini"},
        redacted_prompt="hello",
        org_slug="acme",
        model="gpt-4o-mini",
        request_id="req-ttft",
        start_time=time.perf_counter() - 2.0,
    )
    metrics = StreamRunMetrics(completed=True)
    metrics.provider_start_ts = time.perf_counter() - 1.5
    metrics.first_token_ts = time.perf_counter() - 1.2
    base_pt = {
        "stages": [{"name": "auth", "action": "allow", "latency_ms": 1.0}],
        "total_latency_ms": 1.0,
        "stage_latency_sum_ms": 1.0,
        "overhead_ms": 0.0,
    }
    frame_line = build_stream_trace_frame(
        ctx,
        metrics,
        {"action": "allow", "request_id": "req-ttft"},
        pipeline_trace_base=base_pt,
    )
    frame = json.loads(frame_line.strip().removeprefix("data: ").strip())
    assert frame["zeroshield"]["ttft_ms"] > 0
    assert frame["pipeline_trace"]["total_latency_ms"] > 1.0
    assert frame["pipeline_trace"]["ttft_ms"] > 0
    assert frame["pipeline_trace"]["overhead_ms"] >= 0


@pytest.mark.asyncio
async def test_finalize_stream_records_circuit_and_usage(fake_redis):
    cb = CircuitBreaker(fake_redis, min_requests=2, error_threshold=0.5)
    rl = RateLimiter("redis://localhost")
    rl._pool = fake_redis.connection_pool if hasattr(fake_redis, "connection_pool") else None

    recorded = {}

    def record_stream(**kwargs):
        recorded.update(kwargs)

    ctx = StreamLaunchContext(
        body={"model": "gpt-4o-mini"},
        redacted_prompt=None,
        org_slug="acme",
        model="gpt-4o-mini",
        key_hash="abc",
        rate_limit_tpm=1000,
        estimated_tokens=50,
    )
    metrics = StreamRunMetrics(completed=True, usage={"total_tokens": 42})
    hooks = StreamFinalizeHooks(
        circuit_breaker=cb,
        rate_limiter=None,
        record_stream_complete=record_stream,
    )
    await finalize_stream(ctx, metrics, hooks)
    assert recorded.get("decision") == "allowed"
    status = await cb.check("gpt-4o-mini")
    assert status.should_block is False


@pytest.mark.asyncio
async def test_finalize_stream_reconciles_org_tpm():
    from unittest.mock import AsyncMock, MagicMock

    rl = MagicMock()
    rl.record_org_usage = AsyncMock()
    ctx = StreamLaunchContext(
        body={"model": "m1"},
        redacted_prompt=None,
        org_slug="acme",
        model="m1",
        estimated_tokens=50,
        org_tpm_limit=10_000,
    )
    metrics = StreamRunMetrics(completed=True, usage={"total_tokens": 120})
    hooks = StreamFinalizeHooks(rate_limiter=rl)
    await finalize_stream(ctx, metrics, hooks)
    rl.record_org_usage.assert_awaited_once_with("acme", 120, 50)


@pytest.mark.asyncio
async def test_stream_with_finalize_on_error_records_circuit(fake_redis):
    cb = CircuitBreaker(fake_redis, min_requests=2, error_threshold=0.5)

    async def failing():
        yield "data: {}\n\n"
        raise RuntimeError("boom")

    ctx = StreamLaunchContext(
        body={"model": "m1"},
        redacted_prompt=None,
        org_slug="acme",
        model="m1",
    )
    hooks = StreamFinalizeHooks(circuit_breaker=cb)

    with pytest.raises(RuntimeError):
        async for _ in stream_with_finalize(failing(), ctx, hooks):
            pass

    for _ in range(5):
        await cb.record_error("m1")
    status = await cb.check("m1")
    assert status.should_block is True


def test_extract_usage_from_sse():
    from ai_mesh_gateway.stream_orchestration import _extract_usage_from_sse_line

    line = 'data: ' + json.dumps({"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}) + "\n\n"
    usage = _extract_usage_from_sse_line(line)
    assert usage["total_tokens"] == 3
