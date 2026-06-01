"""HTTP-adjacent integration tests for streaming governance gates."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_mesh_gateway.circuit_breaker import CircuitBreaker
from ai_mesh_gateway.stream_orchestration import streaming_preflight_block_body


@pytest.mark.asyncio
async def test_policy_check_cached_fail_closed_for_stream_path(fake_redis):
    """Mirror proxy_chat policy-unavailable behavior without mounting full app."""
    from ai_mesh_gateway import main as gateway_main

    base_config = dict(gateway_main.CONFIG or {})
    base_config["policy_cache_require_loaded"] = True
    with patch.object(gateway_main, "POLICY_SYNC", None), patch.object(
        gateway_main,
        "CONFIG",
        base_config,
    ):
        code, resp = gateway_main._policy_check_cached("hello", org_slug="acme")
    assert code == 503
    assert resp.get("action") == "block"


@pytest.mark.asyncio
async def test_circuit_open_blocks_before_sse(fake_redis):
    cb = CircuitBreaker(fake_redis, min_requests=1, error_threshold=0.5)
    model = "gpt-4o-mini"
    await cb.record_error(model)
    status = await cb.check(model)
    assert status.should_block is True


@pytest.mark.asyncio
async def test_launch_stream_response_returns_event_stream():
    from ai_mesh_gateway import main as gateway_main

    async def fake_stream(body, redacted_prompt=None, metrics=None):
        yield 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        if metrics is not None:
            metrics.completed = True
        yield "data: [DONE]\n\n"

    request = MagicMock()
    request.headers = {}
    request.client = MagicMock(host="127.0.0.1")

    router = MagicMock()
    router.acompletion_stream = fake_stream

    with patch.object(gateway_main, "LLM_ROUTER", router), patch.object(
        gateway_main, "INPUT_SCANNER", None
    ), patch.object(gateway_main, "OUTPUT_GUARD", None), patch.object(
        gateway_main,
        "CONFIG",
        {"output_scan_enabled": False, "stream_emit_debug_headers": False, "stream_finalize_timeout_ms": 5000},
    ), patch.object(gateway_main, "CIRCUIT_BREAKER", None), patch.object(
        gateway_main, "RATE_LIMITER", None
    ), patch.object(gateway_main, "_emit_telemetry", lambda **_kw: None):
        response = gateway_main._launch_chat_stream_response(
            request=request,
            body={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
            org_config={"output_scan_enabled": False},
            org_slug="acme",
            auth_ctx=None,
            redacted_prompt=None,
            secure_output_scan=False,
        )
        assert response.media_type == "text/event-stream"
        chunks = []
        async for part in response.body_iterator:
            chunks.append(part.decode() if isinstance(part, bytes) else part)
        joined = "".join(chunks)
        assert "data:" in joined
        assert "[DONE]" in joined


def test_stream_preflight_helper_matches_main_block():
    from ai_mesh_gateway.main import _stream_preflight_block_if_needed

    with patch("ai_mesh_gateway.main.POLICY_SYNC", None), patch(
        "ai_mesh_gateway.main.CONFIG",
        {
            "stream_preflight_fail_closed": True,
            "policy_cache_require_loaded": True,
        },
    ):
        resp = _stream_preflight_block_if_needed({})
    assert resp is not None
    assert resp.status_code == 503
    payload = json.loads(resp.body.decode())
    assert payload["code"] == "stream_preflight_policy_unavailable"


def test_injection_block_response_is_json_not_sse():
    """Stream path must return JSON 403 on injection block, never text/event-stream."""
    from ai_mesh_gateway import main as gateway_main

    resp = gateway_main._build_block_response(
        403,
        "tier_1_prompt_injection",
        gateway_main._build_zeroshield_metadata(
            action="block",
            reason="Input blocked by Tier 1 regex scanner.",
            detection_tier="tier_1",
            threat_type="prompt_injection",
            confidence=0.95,
            matched_patterns=["ignore previous instructions"],
            original_prompt="ignore previous instructions",
            detail="Matched injection pattern",
        ),
    )
    assert resp.status_code == 403
    content_type = (resp.headers.get("content-type") or resp.media_type or "").lower()
    assert "event-stream" not in content_type
    assert "json" in content_type or resp.body
    payload = json.loads(resp.body.decode())
    assert payload.get("code") or payload.get("error")
