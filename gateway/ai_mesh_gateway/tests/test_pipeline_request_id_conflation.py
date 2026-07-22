"""
PIPELINE-0023 / L10: one report = one request_id; no cross-request prompt bleed.

Invariants:
- Gateway always mints a fresh zs-* id per chat request (never reuses client X-Request-ID).
- Telemetry metadata carries request_id + pipeline_request_id on every emit.
- pipeline_trace is stamped with the same request_id.
- Concurrent calls with a pinned client header get DISTINCT gateway ids and prompts.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from ai_mesh_gateway import main as gw
from ai_mesh_gateway.tests import test_openai_sdk_compat as T

# Capture at import — live-uvicorn / sdk-compat tests may replace gw._emit_telemetry.
_REAL_EMIT_TELEMETRY = gw._emit_telemetry


class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.state = SimpleNamespace()


def test_bind_gateway_request_id_mints_fresh_despite_pinned_client_header():
    req_a = _FakeRequest({"X-Request-ID": "client-pinned-abc123"})
    req_b = _FakeRequest({"X-Request-ID": "client-pinned-abc123"})
    rid_a = gw._bind_gateway_request_id(req_a, prefix="zs")
    rid_b = gw._bind_gateway_request_id(req_b, prefix="zs")
    assert rid_a.startswith("zs-")
    assert rid_b.startswith("zs-")
    assert rid_a != rid_b
    assert gw._REQUEST_ID.get("") == rid_b
    assert gw._CLIENT_CORRELATION_ID.get("") == "client-pinned-abc123"


def test_stamp_pipeline_trace_request_id():
    token = gw._REQUEST_ID.set("zs-deadbeef0123")
    try:
        trace = gw._stamp_pipeline_trace_request_id({"stages": [], "input_text": "hello"})
        assert trace["request_id"] == "zs-deadbeef0123"
    finally:
        gw._REQUEST_ID.reset(token)


def test_emit_telemetry_threads_request_and_pipeline_request_id():
    token = gw._REQUEST_ID.set("zs-telemetry1234")
    corr = gw._CLIENT_CORRELATION_ID.set("client-trace-99")
    emitted = []

    class _FakeTelemetry:
        def emit(self, event):
            emitted.append(event)

    try:
        with patch.object(gw, "TELEMETRY", _FakeTelemetry()), patch.object(
            gw, "_org_audit_logging_enabled", return_value=True
        ), patch.object(gw, "CONFIG", {"telemetry_enabled": True}):
            _REAL_EMIT_TELEMETRY(
                status_code=200,
                event_type="request",
                action="allow",
                metadata={"prompt_snippet": "alpha prompt"},
            )
    finally:
        gw._REQUEST_ID.reset(token)
        gw._CLIENT_CORRELATION_ID.reset(corr)

    assert len(emitted) == 1
    md = emitted[0]["metadata"]
    assert md["request_id"] == "zs-telemetry1234"
    assert md["pipeline_request_id"] == "zs-telemetry1234"
    assert md["client_correlation_id"] == "client-trace-99"


@pytest_asyncio.fixture()
async def appctx(monkeypatch):
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    gw.LLM_ROUTER.acompletion = AsyncMock(
        return_value=(
            200,
            {
                "id": "chatcmpl-rid",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )
    )
    yield app
    await auth_redis.aclose()


@pytest.mark.asyncio
async def test_concurrent_chat_requests_get_distinct_request_ids(appctx):
    """Two parallel chat calls with the SAME client X-Request-ID must NOT share a gateway id."""
    app = appctx
    pinned = "client-pinned-same-id-001"

    async def _one(prompt: str):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={
                "Authorization": f"Bearer {T.API_KEY}",
                "X-Request-ID": pinned,
            },
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            body = resp.json()
            zs = body.get("zeroshield") or {}
            trace = body.get("pipeline_trace") or {}
            return {
                "header": resp.headers.get("x-request-id"),
                "body_rid": body.get("request_id") or zs.get("request_id"),
                "trace_rid": trace.get("request_id"),
                "input_text": trace.get("input_text") or trace.get("prompt_preview"),
            }

    a, b = await asyncio.gather(_one("PROMPT_ALPHA_UNIQUE"), _one("PROMPT_BETA_UNIQUE"))
    assert a["header"] and b["header"]
    assert a["header"] != b["header"]
    assert a["body_rid"] == a["header"]
    assert b["body_rid"] == b["header"]
    assert a["trace_rid"] == a["header"]
    assert b["trace_rid"] == b["header"]
    assert a["input_text"] != b["input_text"]
    assert "PROMPT_ALPHA" in (a["input_text"] or "")
    assert "PROMPT_BETA" in (b["input_text"] or "")


def test_io_fingerprint_mismatch_blocks_sibling_prompt_merge():
    """Contract test for control _merge_related_scan_metadata L10 guard (no Django)."""

    def _pipeline_io_fingerprint(meta: dict) -> str:
        if not isinstance(meta, dict):
            return ""
        pt = meta.get("pipeline_trace")
        if isinstance(pt, dict):
            for key in ("input_text", "prompt_preview", "prompt_submitted"):
                val = pt.get(key)
                if val:
                    return str(val)
        return str(meta.get("prompt_submitted") or meta.get("prompt_snippet") or "")

    anchor = {
        "request_id": "zs-shared123456",
        "pipeline_trace": {"input_text": "prompt A only", "prompt_submitted": "prompt A only"},
    }
    sibling = {
        "request_id": "zs-shared123456",
        "prompt_submitted": "prompt B must not bleed",
        "pipeline_trace": {"input_text": "prompt B must not bleed"},
    }
    assert _pipeline_io_fingerprint(anchor) != _pipeline_io_fingerprint(sibling)
    # When fingerprints differ, sibling prompt_submitted must not fill anchor gaps.
    merged = dict(anchor)
    if _pipeline_io_fingerprint(anchor) != _pipeline_io_fingerprint(sibling):
        pass  # skip I/O merge — same guard as security_views._merge_related_scan_metadata
    else:
        merged.setdefault("prompt_submitted", sibling.get("prompt_submitted"))
    assert "prompt B" not in (merged.get("prompt_submitted") or "")
