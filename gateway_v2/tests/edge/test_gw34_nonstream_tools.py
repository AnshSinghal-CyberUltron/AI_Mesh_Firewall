"""GW34 Task 8: non-stream tool-call output is validated, enforced and rendered complete."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from tests.edge.test_gw34_upstream_body import _pydantic_tool
from tests.edge.test_gw36_s1_upstream_body import SSN, _chat, _plain

_USER = {"role": "user", "content": "hi"}


class _Scripted:
    """Replies with one event carrying the scripted deltas."""

    def __init__(self, *deltas: tuple[str, str]) -> None:
        self.deltas = deltas
        self.sent: list[Mapping[str, object]] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.sent.append(_plain(request.body["openai_body"]))  # type: ignore[arg-type]
        yield UpstreamEvent(text_deltas=self.deltas, final=True)

    async def abort(self) -> None:
        return None


def _call(*extra: tuple[str, str], name: str = "get_weather", args: str = '{"city":"Paris"}',
          call_id: str = "call_abc") -> tuple[tuple[str, str], ...]:
    return (
        ("output_tool_id#0", call_id), ("output_tool_name#0", name),
        ("output_tool_arguments#0", args), *extra,
    )


def _post(provider: _Scripted, extra: dict[str, Any] | None = None) -> tuple[int, bytes]:
    payload: dict[str, Any] = {"messages": [_USER], "tools": [_pydantic_tool()]}
    payload.update(extra or {})
    return _chat(provider, payload)  # type: ignore[arg-type]


def test_a_valid_tool_call_is_rendered_complete_with_null_content() -> None:
    status, body = _post(_Scripted(*_call(("finish_reason", "tool_calls"))))
    assert status == 200, body
    choice = json.loads(body)["choices"][0]
    assert choice["finish_reason"] == "tool_calls" and choice["message"]["content"] is None
    assert choice["message"]["tool_calls"] == [{
        "id": "call_abc", "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}}]


def test_two_calls_keep_their_arguments_separate_and_in_index_order() -> None:
    deltas = (
        ("output_tool_id#1", "call_b"), ("output_tool_name#1", "get_weather"),
        ("output_tool_arguments#1", '{"b":'), ("output_tool_id#0", "call_a"),
        ("output_tool_name#0", "get_weather"), ("output_tool_arguments#0", '{"a":1}'),
        ("output_tool_arguments#1", "2}"),
    )
    status, body = _post(_Scripted(*deltas))
    assert status == 200, body
    calls = json.loads(body)["choices"][0]["message"]["tool_calls"]
    assert [c["id"] for c in calls] == ["call_a", "call_b"]
    assert [c["function"]["arguments"] for c in calls] == ['{"a":1}', '{"b":2}']


def test_ssn_in_arguments_is_redacted_and_the_arguments_stay_valid_json() -> None:
    status, body = _post(_Scripted(*_call(args=json.dumps({"ssn": SSN, "n": 1}))))
    assert status == 200, body
    arguments = json.loads(body)["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert SSN not in body.decode() and json.loads(arguments) == {"ssn": "[REDACTED]", "n": 1}


def test_an_escape_obfuscated_secret_is_caught_by_the_decoded_form_scan() -> None:
    # `AK\u0049A...` decodes to an AWS key; the raw-text scan does not see it
    args = '{"k": "AK\\u0049AIOSFODNN7EXAMPLE"}'
    provider = _Scripted(*_call(args=args))
    status, body = _post(provider)
    assert status != 200, body
    assert "tool_calls" not in body.decode() and "AKIA" not in body.decode()
    assert json.loads(body)["error"]["code"] == "output_blocked"


@pytest.mark.parametrize(
    ("deltas", "code"),
    [
        (_call(name="rm_rf"), "output_blocked"),                              # undeclared name
        (_call(args="{not json"), "output_blocked"),                          # invalid JSON
        (_call(call_id="bad id!"), "output_blocked"),                         # id shape
        ((("output_tool_arguments#0", "{}"),), "stream_malformed_upstream"),  # arguments only
        ((("output_tool_id#0", "c1"),), "stream_malformed_upstream"),         # id without name
        ((("output_tool_name#0", "get_weather"),), "stream_malformed_upstream"),
        ((("finish_reason", "tool_calls"),), "stream_malformed_upstream"),    # tool_calls, none
    ],
)
def test_invalid_tool_output_releases_nothing(
    deltas: tuple[tuple[str, str], ...], code: str
) -> None:
    status, body = _post(_Scripted(*deltas))
    assert status != 200, body
    text = body.decode()
    assert json.loads(body)["error"]["code"] == code
    assert "tool_calls" not in text and "call_abc" not in text and "Paris" not in text


def test_a_tool_call_when_no_tools_were_declared_is_blocked() -> None:
    status, body = _chat(_Scripted(*_call()), {"messages": [_USER]})  # type: ignore[arg-type]
    assert status != 200 and json.loads(body)["error"]["code"] == "output_blocked"


def test_tool_choice_none_does_not_exempt_the_output_from_validation() -> None:
    status, body = _post(_Scripted(*_call(name="rm_rf")), {"tool_choice": "none"})
    assert status != 200 and json.loads(body)["error"]["code"] == "output_blocked"
    status, body = _post(_Scripted(*_call()), {"tool_choice": "none"})
    assert status == 200  # a declared, valid call is still rendered (known diff, A9)
