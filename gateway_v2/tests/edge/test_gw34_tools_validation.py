"""GW34 Task 4: tool / response_format validation, canonical arguments, parameter partition."""
from __future__ import annotations

import json
import random
from typing import Any

import pytest

from gateway_v2.domain import Surface
from gateway_v2.edge.wire.openai import OpenAIRequestParser
from gateway_v2.edge.wire.openai_codes import (
    MALFORMED_WRONG_SHAPE,
    UNSUPPORTED_CONTENT_PART,
    UNSUPPORTED_PARAMETER,
    MalformedRequest,
)
from gateway_v2.edge.wire.openai_params import REFUSED_FIELDS, accepted_fields
from gateway_v2.edge.wire.openai_tools import SCHEMA_KEYWORDS, canonical_arguments
from gateway_v2.runtime.resources import load_contract

_CONTRACT = load_contract()[0]
_BOUNDS = _CONTRACT.tool_bounds()
_PARSER = OpenAIRequestParser()
_USER = {"role": "user", "content": "hi"}


def _parse(extra: dict[str, Any], messages: list[dict[str, Any]] | None = None) -> Any:
    body = {"model": "m", "messages": messages or [_USER], **extra}
    return _PARSER.parse(json.dumps(body).encode(), surface=Surface.CHAT, contract=_CONTRACT)


def _tool(name: str = "get_weather", **function: Any) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, **function}}


def _refused(extra: dict[str, Any], code: str, param: str, messages: Any = None) -> None:
    with pytest.raises(MalformedRequest) as raised:
        _parse(extra, messages)
    assert (raised.value.code, raised.value.param) == (code, param)


def test_a_realistic_tools_request_is_accepted() -> None:
    schema = {
        "type": "object",
        "title": "Weather",
        "properties": {"city": {"type": "string", "description": "City", "default": "Paris"}},
        "required": ["city"],
        "additionalProperties": False,
        "$defs": {"U": {"type": "string", "enum": ["c", "f"]}},
    }
    req = _parse(
        {
            "tools": [_tool(description="Get the weather", parameters=schema, strict=True)],
            "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
            "parallel_tool_calls": False,
            "response_format": {"type": "json_object"},
        }
    )
    channels = {c for c, _ in req.input_channels}
    assert {"tool_name", "tool_schema_struct", "tool_description", "tool_schema_text"} <= channels
    assert req.upstream_template is not None
    assert req.upstream_template.tools.tools[0].name == "get_weather"


@pytest.mark.parametrize(
    ("extra", "param"),
    [
        ({"tools": "x"}, "tools"),
        ({"tools": [_tool("bad name!")]}, "tools"),
        ({"tools": [{"type": "retrieval"}]}, "tools"),
        ({"tools": [_tool(), _tool()]}, "tools"),
        ({"tools": [_tool(parameters={"type": "object", "x-unknown": 1})]}, "tools"),
        ({"tools": [_tool(parameters={"$ref": "http://evil/x"})]}, "tools"),
        ({"tools": [_tool(parameters={"$ref": "#/$defs/missing"})]}, "tools"),
        ({"tools": [_tool(parameters={"type": "weird"})]}, "tools"),
        ({"tools": [_tool(parameters={"minimum": True})]}, "tools"),
        ({"tools": [_tool(description="a\ue000b")]}, "tools"),
        ({"tools": [_tool(description="\ud800")]}, "tools"),
        ({"tools": [_tool()], "tool_choice": {"type": "function", "function": {"name": "nope"}}},
         "tool_choice"),
        ({"tool_choice": {"type": "function", "function": {"name": "f"}}}, "tool_choice"),
        ({"tool_choice": "sometimes"}, "tool_choice"),
        ({"parallel_tool_calls": "yes"}, "parallel_tool_calls"),
        ({"response_format": {"type": "xml"}}, "response_format"),
        ({"response_format": {"type": "json_schema", "json_schema": {"name": "bad name"}}},
         "response_format"),
    ],
)
def test_malformed_tool_fields_are_declared_400s(extra: dict[str, Any], param: str) -> None:
    _refused(extra, MALFORMED_WRONG_SHAPE, param)


def test_schema_depth_and_node_bounds() -> None:
    node: dict[str, Any] = {"type": "string"}
    for _ in range(_BOUNDS.schema_depth + 2):
        node = {"type": "object", "properties": {"a": node}}
    _refused({"tools": [_tool(parameters=node)]}, MALFORMED_WRONG_SHAPE, "tools")
    too_many = [_tool(f"t{i}") for i in range(_BOUNDS.max_tools + 1)]
    _refused({"tools": too_many}, MALFORMED_WRONG_SHAPE, "tools")


def _call(call_id: str = "call_1", name: str = "get_weather", args: str = '{"a": 1}') -> Any:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def _history(*extra: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _USER,
        {"role": "assistant", "content": None, "tool_calls": [_call()]},
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        *extra,
    ]


def test_tool_round_trip_is_accepted_and_arguments_are_canonical() -> None:
    req = _parse({"tools": [_tool()]}, _history())
    pairs = dict(req.input_channels)
    assert pairs["tool_arguments"] == '{"a":1}'
    assert pairs["tool_arguments_raw"] == '{"a": 1}'
    raw_at = [c for c, _ in req.input_channels].index("tool_arguments_raw")
    assert req.input_forwarded[raw_at] is False
    assert pairs["tool_call_id"].split("\ue000") == ["call_1", "call_1"]


@pytest.mark.parametrize(
    "messages",
    [
        [_USER, {"role": "tool", "tool_call_id": "call_9", "content": "x"}],
        [_USER, {"role": "assistant", "tool_calls": [_call(name="nope")]}],
        [_USER, {"role": "assistant", "tool_calls": [_call(), _call()]}],
        [_USER, {"role": "assistant", "tool_calls": [_call(call_id="bad id!")]}],
        [_USER, {"role": "assistant", "tool_calls": [_call(args="not json")]}],
        [_USER, {"role": "assistant", "tool_calls": [_call(args='{"a": NaN}')]}],
        [_USER, {"role": "assistant", "tool_calls": [_call(args='{"a": 1e400}')]}],
        [_USER, {"role": "assistant", "tool_calls": [{"id": "c"}]}],
    ],
)
def test_tool_message_ties_and_argument_shapes_are_refused(messages: list[dict[str, Any]]) -> None:
    _refused({"tools": [_tool()]}, MALFORMED_WRONG_SHAPE, "messages", messages)


def test_tool_calls_on_a_non_assistant_role_are_unsupported() -> None:
    _refused({}, UNSUPPORTED_PARAMETER, "messages", [{"role": "user", "tool_calls": [_call()]}])


def test_the_name_tie_is_skipped_when_no_tools_are_declared() -> None:
    req = _parse({}, [_USER, {"role": "assistant", "tool_calls": [_call(name="anything")]}])
    assert "tool_arguments" in dict(req.input_channels)


@pytest.mark.parametrize(
    ("text", "want"),
    [("", "{}"), ("  \n", "{}"), ('{"b": 1,  "a": [1, 2]}', '{"b":1,"a":[1,2]}'),
     ('{"k": "\\u0041KIA"}', '{"k":"AKIA"}'), ('{"é": "é"}', '{"é":"é"}')],
)
def test_canonical_arguments(text: str, want: str) -> None:
    assert canonical_arguments(text, _BOUNDS) == want


@pytest.mark.parametrize("text", ["{", "[1,", '{"a": Infinity}', '{"a": "\\ud800"}', "x" * 10**7])
def test_canonical_arguments_refuses(text: str) -> None:
    with pytest.raises(MalformedRequest):
        canonical_arguments(text, _BOUNDS)


def test_canonical_arguments_depth_bomb_is_a_refusal_not_a_recursion_error() -> None:
    with pytest.raises(MalformedRequest):
        canonical_arguments("[" * 100000 + "]" * 100000, _BOUNDS)


@pytest.mark.parametrize(
    ("extra", "code", "param"),
    [
        ({"functions": []}, UNSUPPORTED_PARAMETER, "functions"),
        ({"function_call": "auto"}, UNSUPPORTED_PARAMETER, "function_call"),
        ({"logprobs": True}, UNSUPPORTED_PARAMETER, "logprobs"),
        ({"top_logprobs": 2}, UNSUPPORTED_PARAMETER, "top_logprobs"),
        ({"audio": {}}, UNSUPPORTED_PARAMETER, "audio"),
        ({"modalities": ["text"]}, UNSUPPORTED_PARAMETER, "modalities"),
        ({"prediction": {}}, UNSUPPORTED_PARAMETER, "prediction"),
        ({"web_search_options": {}}, UNSUPPORTED_PARAMETER, "web_search_options"),
    ],
)
def test_declared_refusals_stay_refused(extra: dict[str, Any], code: str, param: str) -> None:
    _refused(extra, code, param)


@pytest.mark.parametrize("extra", [{"logprobs": False}, {"logprobs": None}, {"top_logprobs": None}])
def test_falsey_logprobs_are_accepted_and_not_forwarded(extra: dict[str, Any]) -> None:
    req = _parse(extra)
    assert "logprobs" not in req.forwarded and "top_logprobs" not in req.forwarded


@pytest.mark.parametrize(
    ("messages", "code"),
    [
        ([{"role": "function", "content": "x", "name": "f"}], UNSUPPORTED_PARAMETER),
        ([{"role": "assistant", "function_call": {"name": "f", "arguments": "{}"}}],
         UNSUPPORTED_PARAMETER),
        ([{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}],
         UNSUPPORTED_CONTENT_PART),
        ([{"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]}],
         UNSUPPORTED_CONTENT_PART),
        ([{"role": "user", "content": [{"type": "file", "file": {}}]}], UNSUPPORTED_CONTENT_PART),
    ],
)
def test_deprecated_and_non_text_message_shapes_stay_refused(
    messages: list[dict[str, Any]], code: str
) -> None:
    _refused({}, code, "messages", messages)


def test_tools_disabled_route_refuses_tool_bearing_requests() -> None:
    """A contract with no tool_bounds (the standalone fakes) refuses, fail closed."""

    class _NoTools:
        @staticmethod
        def max_output_tokens_boundary() -> int:
            return _CONTRACT.max_output_tokens_boundary()

    for extra in ({"tools": [_tool()]}, {"response_format": {"type": "json_object"}}):
        body = {"model": "m", "messages": [_USER], **extra}
        with pytest.raises(MalformedRequest) as raised:
            _PARSER.parse(json.dumps(body).encode(), surface=Surface.CHAT, contract=_NoTools())  # type: ignore[arg-type]
        assert raised.value.code == UNSUPPORTED_PARAMETER


def test_the_schema_keyword_table_is_closed_and_covers_pydantic_output() -> None:
    for keyword in ("$defs", "$ref", "anyOf", "additionalProperties", "items", "required",
                    "title", "default", "description", "enum"):
        assert keyword in SCHEMA_KEYWORDS
    with pytest.raises(TypeError):
        SCHEMA_KEYWORDS["x"] = "y"  # type: ignore[index]


def test_property_8_accepted_and_refused_fields_partition_a_field_corpus() -> None:
    # Feature: gw34-sdk-compat, Property 8: accepted/refused request fields partition the corpus
    rng = random.Random(0x6A34_0008)
    accepted = accepted_fields()
    assert not accepted & REFUSED_FIELDS
    corpus = [*accepted, *REFUSED_FIELDS]
    corpus += ["".join(rng.choice("abcdefghijklmnopqrstuvwxyz_") for _ in range(rng.randint(3, 14)))
               for _ in range(200)]
    for field in corpus:
        body: dict[str, Any] = {"model": "m", "messages": [_USER]}
        if field not in body:
            body[field] = None if field in accepted else {"x": 1}
        if field == "stream":
            body[field] = False
        in_accepted = field in accepted
        try:
            _PARSER.parse(json.dumps(body).encode(), surface=Surface.CHAT, contract=_CONTRACT)
            parsed_ok = True
        except MalformedRequest as exc:
            parsed_ok = False
            assert in_accepted or exc.code == UNSUPPORTED_PARAMETER, field
        if not in_accepted:
            assert not parsed_ok, field
    # adding an accepted field without scanning it fails: every accepted field is a known one
    scanned_or_typed = {
        "model", "messages", "prompt", "stop", "max_tokens", "max_completion_tokens", "n",
        "stream", "stream_options", "user", "metadata", "store", "service_tier", "temperature",
        "top_p", "presence_penalty", "frequency_penalty", "seed", "logit_bias",
        "reasoning_effort", "tools", "tool_choice", "parallel_tool_calls", "response_format",
        "logprobs", "top_logprobs",
    }
    assert accepted == scanned_or_typed
