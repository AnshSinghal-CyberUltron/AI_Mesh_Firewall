"""GW34 Task 9: a refusal or failure before the first byte is a plain HTTP error (R7, P5)."""
from __future__ import annotations

import json
import random
from typing import Any

import pytest

from gateway_v2.dispatch.provider import UpstreamEvent
from tests.edge.gw34_stream_harness import Reply, Scripted, call, content, deltas, failure

_USER = {"role": "user", "content": "hi"}
_SECRET = "key AKIAIOSFODNN7EXAMPLE tail"


def _stream(provider: Scripted, **extra: Any) -> Reply:
    return call(provider, {"messages": [_USER], "stream": True, **extra})


def _plain_error(reply: Reply, code: str) -> None:
    assert reply.status != 200 and not reply.is_sse, (reply.status, reply.headers)
    assert reply.headers["content-type"].startswith("application/json")
    assert json.loads(reply.body)["error"]["code"] == code
    assert b"amf.trace" not in reply.body and b"data:" not in reply.body


def test_a_role_only_chunk_then_an_upstream_timeout_is_a_plain_http_error() -> None:
    role_only = UpstreamEvent(text_deltas=(("role", "assistant"),), final=False)
    reply = _stream(Scripted([role_only, failure("upstream_timeout")]))
    _plain_error(reply, "upstream_timeout")
    assert reply.status == 504


def test_a_role_chunk_then_a_first_window_output_block_is_a_plain_output_blocked() -> None:
    role_only = UpstreamEvent(text_deltas=(("role", "assistant"),), final=False)
    reply = _stream(Scripted([role_only, content(_SECRET, final=True)]))
    _plain_error(reply, "output_blocked")
    assert reply.status == 403 and b"AKIA" not in reply.body


def test_a_thousand_empty_keep_alives_then_a_failure_is_still_a_plain_http_error() -> None:
    empty = UpstreamEvent(text_deltas=(("content", ""),), final=False)
    reply = _stream(Scripted([*([empty] * 1000), failure("upstream_unavailable")]))
    _plain_error(reply, "upstream_unavailable")


def test_empty_text_and_meta_only_frames_do_not_start_the_response() -> None:
    meta = deltas(("finish_reason", "stop"))
    reply = _stream(Scripted([meta, failure("upstream_rate_limited")]))
    _plain_error(reply, "upstream_rate_limited")
    assert reply.status == 429


def test_a_clean_stream_with_no_content_is_200_with_a_finish_chunk_and_done() -> None:
    reply = _stream(Scripted([UpstreamEvent(text_deltas=(), final=True)]))
    assert reply.status == 200 and reply.is_sse
    assert reply.deltas() == [{}]
    assert reply.frames()[-1]["choices"][0]["finish_reason"] == "stop"
    assert reply.body.rstrip().endswith(b"data: [DONE]")


def test_a_healthy_stream_starts_at_the_first_frame_and_the_status_is_decided_then() -> None:
    reply = _stream(Scripted([content("hello ", final=False), content("world", final=True)]))
    kinds = [m["type"] for m in reply.messages]
    assert kinds[0] == "http.response.start" and kinds[1] == "http.response.body"
    first = reply.messages[1]["body"]
    assert b'"role":"assistant"' in first and b"hello " in first  # the first released frame
    assert reply.status == 200 and reply.is_sse


def test_after_the_first_byte_a_failure_is_an_sse_error_frame_as_before() -> None:
    reply = _stream(Scripted([content("part one ", final=False), failure("upstream_timeout")]))
    assert reply.status == 200 and reply.is_sse
    assert b"upstream_timeout" in reply.body and reply.body.count(b"[DONE]") == 0


def test_property_5_every_refusal_is_plain_http_never_200_event_stream() -> None:
    # Feature: gw34-sdk-compat, Property 5: a pre-first-byte refusal is a plain HTTP error
    rng = random.Random(0x6A34_0005)
    refusals: list[tuple[dict[str, Any], str]] = [
        ({"audio": {}}, "unsupported_parameter"),
        ({"functions": []}, "unsupported_parameter"),
        ({"logprobs": True}, "unsupported_parameter"),
        ({"tools": "x"}, "malformed_request_wrong_shape"),
        ({"tool_choice": "weird"}, "malformed_request_wrong_shape"),
        ({"response_format": {"type": "nope"}}, "malformed_request_wrong_shape"),
        ({"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]},
         "unsupported_content_part"),
        ({"messages": [{"role": "user", "content": "key AKIAIOSFODNN7EXAMPLE"}]}, "input_blocked"),
        ({"messages": [{"role": "tool", "tool_call_id": "x", "content": "y"}]},
         "malformed_request_wrong_shape"),
        ({"max_tokens": 10**9}, "invalid_max_tokens"),
    ]
    for _ in range(100):
        extra, code = rng.choice(refusals)
        provider = Scripted([content("never", final=True)])
        reply = call(provider, {"messages": [_USER], "stream": rng.random() < 0.5, **extra})
        assert reply.status != 200 and not reply.is_sse, (extra, reply.status)
        assert json.loads(reply.body)["error"]["code"] == code, extra
        assert provider.opened == 0, "a refused request must make zero provider calls"


@pytest.mark.parametrize("stream", [False, True])
def test_a_stream_flag_does_not_change_the_refusal_bytes(stream: bool) -> None:
    reply = call(Scripted([]), {"messages": [_USER], "stream": stream, "audio": {}})
    body = json.loads(reply.body)
    assert reply.status == 400 and body["error"]["code"] == "unsupported_parameter"
