"""GW34 Task 5: the SSE decoder's tool-call / finish_reason handling (one test per bad shape)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from gateway_v2.edge.wire.sse import SSEDecoder


def _event(delta: dict[str, Any] | None = None, **choice: Any) -> bytes:
    body = {"id": "x", "object": "chat.completion.chunk", "model": "m", "created": 1,
            "choices": [{"index": 0, "delta": delta or {}, **choice}]}
    return b"data: " + json.dumps(body).encode() + b"\n\n"


def _decode(raw: bytes, max_calls: int = 8) -> tuple[SSEDecoder, list[tuple[str, str]]]:
    decoder = SSEDecoder(max_tool_calls=max_calls)
    deltas = [d for frame in decoder.feed(raw) for d in frame.text_deltas]
    return decoder, deltas


def _call(index: int = 0, **fields: Any) -> dict[str, Any]:
    return {"tool_calls": [{"index": index, **fields}]}


def test_tool_call_fragments_become_per_index_channels() -> None:
    raw = _event(_call(0, id="call_1", type="function", function={"name": "f", "arguments": ""}))
    raw += _event(_call(0, function={"arguments": '{"a"'}))
    raw += _event(_call(1, id="call_2", function={"name": "g", "arguments": "{}"}))
    raw += _event(_call(0, function={"arguments": ":1}"}), finish_reason="tool_calls")
    decoder, deltas = _decode(raw)
    assert not decoder.malformed()
    assert deltas == [
        ("output_tool_id#0", "call_1"), ("output_tool_name#0", "f"),
        ("output_tool_arguments#0", '{"a"'),
        ("output_tool_id#1", "call_2"), ("output_tool_name#1", "g"),
        ("output_tool_arguments#1", "{}"),
        ("output_tool_arguments#0", ":1}"), ("finish_reason", "tool_calls"),
    ]


def test_a_repeated_equal_id_and_name_is_dropped() -> None:
    raw = _event(_call(0, id="c", function={"name": "f"})) + _event(
        _call(0, id="c", function={"name": "f", "arguments": "{}"})
    )
    decoder, deltas = _decode(raw)
    assert not decoder.malformed()
    assert deltas == [
        ("output_tool_id#0", "c"), ("output_tool_name#0", "f"), ("output_tool_arguments#0", "{}")
    ]


@pytest.mark.parametrize(
    "delta",
    [
        {"tool_calls": "x"},
        {"tool_calls": [1]},
        _call(-1, id="c"),
        _call(8, id="c"),
        {"tool_calls": [{"id": "c"}]},
        {"tool_calls": [{"index": True, "id": "c"}]},
        {"tool_calls": [{"index": 1.5, "id": "c"}]},
        _call(0, id=5),
        _call(0, type="retrieval"),
        _call(0, function="x"),
        _call(0, function={"name": 3}),
        _call(0, function={"arguments": 3}),
        {"function_call": {"name": "f"}},
        {"finish_reason": "stop"},
        {"output_tool_name#0": "f"},
        {"output_tool_arguments#99999": "x"},
        {"output_tool_x": "y"},
        {"a#b": "y"},
    ],
)
def test_bad_shapes_and_reserved_keys_latch_malformed(delta: dict[str, Any]) -> None:
    decoder, deltas = _decode(_event(delta))
    assert decoder.malformed() and deltas == []


def test_a_differing_repeat_of_id_or_name_is_malformed() -> None:
    first = _event(_call(0, id="a", function={"name": "f"}))
    for repeat in (_call(0, id="b"), _call(0, function={"name": "g"})):
        assert _decode(first + _event(repeat))[0].malformed()


def test_max_tool_calls_zero_makes_any_tool_calls_delta_malformed() -> None:
    assert _decode(_event(_call(0, id="c")), max_calls=0)[0].malformed()
    assert not _decode(_event({"content": "hi"}), max_calls=0)[0].malformed()


@pytest.mark.parametrize("reason", ["stop", "length", "tool_calls", "content_filter"])
def test_finish_reason_in_the_closed_set_becomes_a_meta_delta(reason: str) -> None:
    decoder, deltas = _decode(_event({}, finish_reason=reason))
    assert not decoder.malformed() and deltas == [("finish_reason", reason)]


@pytest.mark.parametrize("reason", ["function_call", "weird", 3, ""])
def test_finish_reason_outside_the_closed_set_is_malformed(reason: Any) -> None:
    assert _decode(_event({}, finish_reason=reason))[0].malformed()


def test_choice_index_one_is_ignored_and_a_non_int_index_is_malformed() -> None:
    body = {"choices": [{"index": 0, "delta": {"content": "a"}},
                        {"index": 1, "delta": {"content": "LEAK"}}]}
    decoder = SSEDecoder()
    frames = decoder.feed(b"data: " + json.dumps(body).encode() + b"\n\n")
    assert [d for f in frames for d in f.text_deltas] == [("content", "a")]
    for bad in ("0", True, 1.0, None):
        raw = b"data: " + json.dumps({"choices": [{"index": bad, "delta": {}}]}).encode() + b"\n\n"
        d = SSEDecoder()
        d.feed(raw)
        assert d.malformed(), bad


def test_content_reasoning_and_refusal_keep_their_channels() -> None:
    _, deltas = _decode(_event({"role": "assistant", "content": "a", "reasoning_content": "r",
                                "refusal": "no"}))
    assert deltas == [("role", "assistant"), ("content", "a"), ("reasoning_content", "r"),
                      ("refusal", "no")]
