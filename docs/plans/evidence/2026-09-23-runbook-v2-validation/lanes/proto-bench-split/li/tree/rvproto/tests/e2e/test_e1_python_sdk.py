"""E1: official OpenAI Python SDK (openai==2.38.0), sync + AsyncOpenAI, against rvproto."""

from __future__ import annotations

import json

import httpx
import openai
import pytest

from tests.e2e.conftest import (
    INJECTION,
    KEY_A,
    KEY_Q,
    MODEL,
    TOOLS,
    reference_content,
    rid,
)

MSG = [{"role": "user", "content": "Say hello politely."}]


def test_sdk_version_pinned() -> None:
    assert openai.__version__ == "2.38.0"


def _sync(base: str, key: str = KEY_A) -> openai.OpenAI:
    return openai.OpenAI(base_url=f"{base}/v1", api_key=key, max_retries=0)


def _async(base: str, key: str = KEY_A) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(base_url=f"{base}/v1", api_key=key, max_retries=0)


def test_sync_non_stream(base: str) -> None:
    r = rid("sync-json")
    raw = _sync(base).chat.completions.with_raw_response.create(
        model=MODEL, messages=MSG, max_tokens=12, extra_headers={"x-request-id": r})
    c = raw.parse()
    assert raw.headers["x-request-id"] == r and raw.headers["x-rv-disposition"] == "ALLOW"
    assert c.object == "chat.completion" and c.choices[0].finish_reason == "stop"
    assert c.usage is not None and c.usage.completion_tokens == 12
    assert c.choices[0].message.content == reference_content(r, {"model": MODEL, "messages": MSG,
                                                                 "max_tokens": 12})


def test_sync_stream(base: str) -> None:
    r = rid("sync-sse")
    stream = _sync(base).chat.completions.create(
        model=MODEL, messages=MSG, max_tokens=15, stream=True,
        stream_options={"include_usage": True}, extra_headers={"x-request-id": r})
    chunks = list(stream)  # iteration ends only on [DONE]
    text = "".join(ch.choices[0].delta.content or "" for ch in chunks if ch.choices)
    assert chunks[-1].usage is not None and chunks[-1].usage.completion_tokens == 15
    finish = [ch.choices[0].finish_reason for ch in chunks if ch.choices and ch.choices[0].finish_reason]
    assert finish == ["stop"]
    assert text == reference_content(r, {"model": MODEL, "messages": MSG, "max_tokens": 15})


@pytest.mark.asyncio
async def test_async_non_stream_and_stream(base: str) -> None:
    client = _async(base)
    try:
        r1 = rid("async-json")
        c = await client.chat.completions.create(model=MODEL, messages=MSG, max_tokens=9,
                                                 extra_headers={"x-request-id": r1})
        assert c.choices[0].message.content == reference_content(
            r1, {"model": MODEL, "messages": MSG, "max_tokens": 9})
        r2 = rid("async-sse")
        s = await client.chat.completions.create(model=MODEL, messages=MSG, max_tokens=9, stream=True,
                                                 extra_headers={"x-request-id": r2})
        text = "".join([ch.choices[0].delta.content or "" async for ch in s if ch.choices])
        assert text == reference_content(r2, {"model": MODEL, "messages": MSG, "max_tokens": 9})
    finally:
        await client.close()


def _reference_tool_args(request_id: str) -> str:
    r = httpx.post(f"{_provider()}/v1/chat/completions",
                   json={"model": MODEL, "messages": [{"role": "user", "content": "weather?"}],
                         "tools": TOOLS, "stream": True},
                   headers={"x-request-id": request_id, "x-synth-tool": "1"}, timeout=60)
    args = ""
    for line in r.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            d = json.loads(line[6:])
            for ch in d.get("choices") or []:
                for tc in (ch.get("delta") or {}).get("tool_calls") or []:
                    args += (tc.get("function") or {}).get("arguments") or ""
    return args


def _provider() -> str:
    from tests.e2e.conftest import PROVIDER

    return PROVIDER


def test_tool_call_stream_fragments_reconstructed_exactly(base: str) -> None:
    r = rid("tools")
    client = _sync(base)
    fragments: list[str] = []
    with client.chat.completions.stream(
        model=MODEL, messages=[{"role": "user", "content": "weather?"}], tools=TOOLS,
        extra_headers={"x-request-id": r, "x-synth-tool": "1"},
    ) as stream:
        for event in stream:
            if event.type == "tool_calls.function.arguments.delta":
                fragments.append(event.arguments_delta)
        final = stream.get_final_completion()
    call = final.choices[0].message.tool_calls[0]
    assert final.choices[0].finish_reason == "tool_calls"
    assert call.function.name == "get_weather" and call.id
    expected = _reference_tool_args(r)
    assert len(fragments) >= 5
    assert "".join(fragments) == call.function.arguments == expected
    json.loads(expected)


@pytest.mark.parametrize("stream", [False, True])
def test_block_is_typed_403_with_envelope_and_zero_sse_bytes(base: str, stream: bool) -> None:
    r = rid(f"block-{stream}")
    with pytest.raises(openai.PermissionDeniedError) as ei:
        res = _sync(base).chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": INJECTION}], stream=stream,
            extra_headers={"x-request-id": r})
        if stream:
            list(res)  # would only run if the SDK accepted a stream
    e = ei.value
    assert isinstance(e, openai.APIStatusError) and e.status_code == 403
    assert e.code == "blocked_by_policy" and e.type == "policy_violation" and e.param is None
    assert e.request_id == r and isinstance(e.body, dict) and "message" in e.body
    raw = httpx.post(f"{base}/v1/chat/completions", headers={"authorization": f"Bearer {KEY_A}"},
                     json={"model": MODEL, "stream": stream,
                           "messages": [{"role": "user", "content": INJECTION}]})
    assert raw.status_code == 403 and raw.headers["content-type"].startswith("application/json")
    assert b"data:" not in raw.content and set(raw.json()) == {"error"}
    assert set(raw.json()["error"]) == {"message", "type", "param", "code"}


@pytest.mark.asyncio
async def test_bad_key_is_authentication_error(base: str) -> None:
    with pytest.raises(openai.AuthenticationError) as ei:
        _sync(base, "sk-bogus").chat.completions.create(model=MODEL, messages=MSG)
    assert ei.value.status_code == 401 and ei.value.code == "invalid_api_key"
    client = _async(base, "sk-bogus")
    try:
        with pytest.raises(openai.AuthenticationError):
            await client.chat.completions.create(model=MODEL, messages=MSG, stream=True)
    finally:
        await client.close()


def test_quota_exhaustion_is_rate_limit_error(base: str) -> None:
    """org-q has a 2,500-token org budget (shared lease); max_tokens=1000 per request."""
    import redis

    from tests.e2e.conftest import REDIS_URL

    redis.Redis.from_url(REDIS_URL).set("rv:budget:org-q", 2500)  # self-contained rerun
    client = _sync(base, KEY_Q)
    ok = 0
    with pytest.raises(openai.RateLimitError) as ei:
        for _ in range(10):
            client.chat.completions.create(model=MODEL, messages=MSG, max_tokens=1000)
            ok += 1
    assert ei.value.status_code == 429 and ei.value.code == "insufficient_quota"
    assert 1 <= ok <= 3, ok


def test_models_list(base: str) -> None:
    models = _sync(base).models.list()
    assert [m.id for m in models.data] == [MODEL]
