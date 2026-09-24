"""Assertions ported from gateway_v2/tests/openai_conformance (test_openai_sdk_compat*.py and the
live-uvicorn file), re-targeted EXPLICITLY at rvproto over TCP: every client is
AsyncOpenAI(base_url=f"{BASE}/v1") or httpx against BASE -- nothing imports an app in-process.
Provider-specific content ("Hello from upstream.") is replaced by the deterministic provider's
reference content for the same request id. Block status is 403 (PROTO_SPEC), not v1's 400.
"""

from __future__ import annotations

import json

import httpx
import openai
import pytest
import redis

from tests.e2e.conftest import INJECTION, KEY_A, KEY_B, KEY_Q, MODEL, REDIS_URL, TOOLS, reference_content, rid

pytestmark = pytest.mark.asyncio
MSG = [{"role": "user", "content": "Say hello politely."}]


def _client(base: str, key: str = KEY_A) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(base_url=f"{base}/v1", api_key=key, max_retries=0)


async def test_non_streaming_parses_as_chat_completion(base: str) -> None:
    r = rid("p-json")
    async with _client(base) as c:
        comp = await c.chat.completions.create(model=MODEL, messages=MSG, max_tokens=7,
                                               extra_headers={"x-request-id": r})
    assert comp.object == "chat.completion" and comp.id
    assert comp.choices[0].message.content == reference_content(r, {"model": MODEL, "messages": MSG, "max_tokens": 7})
    u = comp.usage
    assert u is not None and u.total_tokens == u.prompt_tokens + u.completion_tokens


async def test_streaming_chunks_and_done(base: str) -> None:
    r = rid("p-sse")
    async with _client(base) as c:
        stream = await c.chat.completions.create(model=MODEL, messages=MSG, max_tokens=9, stream=True,
                                                 stream_options={"include_usage": True},
                                                 extra_headers={"x-request-id": r})
        chunks = [ch async for ch in stream]  # [DONE] must terminate iteration cleanly
    assert all(ch.object == "chat.completion.chunk" for ch in chunks)
    assert len({ch.id for ch in chunks}) == 1
    content = "".join(ch.choices[0].delta.content or "" for ch in chunks if ch.choices and ch.choices[0].delta)
    assert content == reference_content(r, {"model": MODEL, "messages": MSG, "max_tokens": 9})
    assert chunks[-1].usage is not None and chunks[-1].usage.completion_tokens == 9


async def test_raw_sse_framing_and_done_over_tcp(base: str) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=30,
                                 headers={"authorization": f"Bearer {KEY_A}"}) as rc:
        async with rc.stream("POST", "/v1/chat/completions",
                             json={"model": MODEL, "messages": MSG, "stream": True, "max_tokens": 6}) as resp:
            assert resp.status_code == 200 and "text/event-stream" in resp.headers["content-type"]
            assert resp.headers.get("x-request-id")
            raw = b"".join([b async for b in resp.aiter_bytes()])
    events = raw.split(b"\n\n")
    assert events[-1] == b"" and events[-2] == b"data: [DONE]"
    for e in events[:-2]:
        assert e.startswith(b"data: ") and json.loads(e[6:])["object"] == "chat.completion.chunk"


@pytest.mark.parametrize("stream", [False, True])
async def test_blocked_request_raises_before_sse(base: str, stream: bool) -> None:
    async with _client(base) as c:
        with pytest.raises(openai.APIStatusError) as ei:
            await c.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": INJECTION}],
                                            stream=stream)
    err = ei.value
    assert err.status_code == 403  # PROTO_SPEC; the v1 suite asserts 400 content_filter
    assert err.code is not None and err.type is not None and err.message  # D2
    assert err.request_id  # D3
    assert err.param is None  # A1: a content block is not a parameter error


async def test_invalid_api_key_raises_authentication_error(base: str) -> None:
    async with _client(base, "zs_test_wrong_key_0123456789abcdef") as c:
        with pytest.raises(openai.AuthenticationError) as ei:
            await c.chat.completions.create(model=MODEL, messages=MSG)
    assert ei.value.request_id


async def test_chat_tool_calls_forwarded_and_parsed(base: str) -> None:
    async with _client(base) as c:
        comp = await c.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": "Weather?"}],
                                               tools=TOOLS, tool_choice="auto", extra_headers={"x-synth-tool": "1"})
    choice = comp.choices[0]
    assert choice.finish_reason == "tool_calls" and choice.message.tool_calls
    tc = choice.message.tool_calls[0]
    assert tc.type == "function" and tc.function.name == "get_weather" and tc.id
    assert isinstance(json.loads(tc.function.arguments), dict)


async def test_models_list_returns_ids(base: str) -> None:
    async with _client(base) as c:
        page = await c.models.list()
    assert MODEL in [m.id for m in page.data]


@pytest.mark.parametrize(("extra", "param"), [({"messages": "not-a-list"}, "messages"), ({"model": 123}, "model")])
async def test_error_param_populated_on_bad_body(base: str, extra: dict[str, object], param: str) -> None:
    async with _client(base) as c:
        with pytest.raises(openai.BadRequestError) as ei:
            await c.chat.completions.create(model=MODEL, messages=MSG, extra_body=extra)
    assert ei.value.status_code == 400 and ei.value.param == param and ei.value.request_id


async def test_error_request_id_present_across_error_classes(base: str) -> None:
    seen: dict[int, str | None] = {}
    async with _client(base, "sk-bogus") as bad:
        with pytest.raises(openai.AuthenticationError) as e401:
            await bad.chat.completions.create(model=MODEL, messages=MSG)
        seen[401] = e401.value.request_id
    async with _client(base) as c:
        with pytest.raises(openai.PermissionDeniedError) as e403:
            await c.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": INJECTION}])
        seen[403] = e403.value.request_id
        with pytest.raises(openai.BadRequestError) as e400:
            await c.chat.completions.create(model=MODEL, messages=MSG, extra_body={"messages": "x"})
        seen[400] = e400.value.request_id
    redis.Redis.from_url(REDIS_URL).set("rv:budget:org-q", 0)
    async with _client(base, KEY_Q) as q:
        with pytest.raises(openai.RateLimitError) as e429:
            await q.chat.completions.create(model=MODEL, messages=MSG, max_tokens=10_000)
        seen[429] = e429.value.request_id
    assert all(seen.values()), seen


async def test_unimplemented_surface_returns_clean_404(base: str) -> None:
    async with _client(base) as c:
        with pytest.raises(openai.NotFoundError) as ei:
            await c.images.generate(model="dall-e-3", prompt="hello")
    assert ei.value.status_code == 404 and ei.value.request_id


async def test_unmatched_route_and_malformed_json_are_nested_envelopes(base: str) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=15, headers={"authorization": f"Bearer {KEY_B}"}) as rc:
        r404 = await rc.post("/v1/frobnicate", json={"x": 1})
        r400 = await rc.post("/v1/chat/completions", content=b"{not valid json",
                             headers={"content-type": "application/json"})
    for r, status in ((r404, 404), (r400, 400)):
        assert r.status_code == status and r.headers.get("x-request-id")
        body = r.json()
        assert isinstance(body.get("error"), dict) and body["error"]["message"] and body["error"]["type"]
        assert "text/html" not in r.headers.get("content-type", "")


async def test_client_disconnect_midstream_then_server_responsive(base: str) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=15, headers={"authorization": f"Bearer {KEY_A}"}) as rc:
        async with rc.stream("POST", "/v1/chat/completions",
                             json={"model": MODEL, "messages": MSG, "stream": True, "max_tokens": 50}) as resp:
            async for _line in resp.aiter_lines():
                break
    async with httpx.AsyncClient(base_url=base, timeout=15, headers={"authorization": f"Bearer {KEY_A}"}) as rc:
        r2 = await rc.post("/v1/chat/completions", json={"model": MODEL, "messages": MSG, "max_tokens": 3})
    assert r2.status_code == 200


async def test_zero_token_request_returns_promptly(base: str) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=15, headers={"authorization": f"Bearer {KEY_A}"}) as rc:
        r = await rc.post("/v1/chat/completions", json={"model": MODEL, "messages": MSG, "max_tokens": 0})
    assert r.status_code < 500 and r.headers.get("x-request-id")
