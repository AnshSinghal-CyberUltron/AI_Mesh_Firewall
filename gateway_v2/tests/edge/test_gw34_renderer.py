"""GW34 Task 5: the non-stream renderer builds complete SDK-valid tool_calls."""
from __future__ import annotations

import json
from typing import Any

from gateway_v2.domain import Surface
from gateway_v2.edge.wire.openai import CanonicalRequest, OpenAIResponseRenderer

_REQ = CanonicalRequest(model="m", streaming=False, input_channels=(), text_deltas=(),
                        surface=Surface.CHAT)


def _render(deltas: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    return json.loads(OpenAIResponseRenderer().non_stream_body(_REQ, deltas))["choices"][0]


def test_complete_tool_calls_in_index_order_with_null_content() -> None:
    choice = _render((
        ("output_tool_id#1", "call_b"), ("output_tool_name#1", "search"),
        ("output_tool_arguments#1", '{"q":'), ("output_tool_id#0", "call_a"),
        ("output_tool_name#0", "get_weather"), ("output_tool_arguments#0", "{}"),
        ("output_tool_arguments#1", '"x"}'),
    ))
    assert choice["message"]["content"] is None
    assert choice["message"]["tool_calls"] == [
        {"id": "call_a", "type": "function",
         "function": {"name": "get_weather", "arguments": "{}"}},
        {"id": "call_b", "type": "function",
         "function": {"name": "search", "arguments": '{"q":"x"}'}},
    ]
    assert choice["finish_reason"] == "tool_calls"


def test_plain_completion_keeps_a_string_content_and_stop() -> None:
    choice = _render((("output_content", "hi"),))
    assert choice["message"] == {"role": "assistant", "content": "hi"}
    assert choice["finish_reason"] == "stop"


def test_the_meta_finish_reason_wins_and_text_with_tool_calls_keeps_content() -> None:
    choice = _render((
        ("content", "thinking"), ("output_tool_id#0", "c"), ("output_tool_name#0", "f"),
        ("finish_reason", "length"),
    ))
    assert choice["message"]["content"] == "thinking" and choice["finish_reason"] == "length"
    assert choice["message"]["tool_calls"][0]["function"] == {"name": "f", "arguments": ""}
