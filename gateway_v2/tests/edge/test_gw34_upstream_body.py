"""GW34 Task 7: tool fields reach the provider rendered from the template, scanned and verified.

End to end through the dev-profile pipeline (real detectors, real resolver, real redaction
verification) with a provider that records the rendered request. Covers fidelity of a realistic
pydantic-style ``tools`` request, history round trips, redaction, the BLOCK-only channels (P3),
redaction never forwarding invalid JSON arguments (P4 request side), the realistic-id
false-positive measurement, and scan-slot headroom.
"""
from __future__ import annotations

import json
import random
import uuid
from typing import Any

import pytest

from gateway_v2.domain import Surface
from gateway_v2.edge.wire.openai import OpenAIRequestParser
from tests.edge.test_gw36_s1_upstream_body import SSN, _chat, _contract, _Recorder

_USER = {"role": "user", "content": "hi"}
_EMAIL = "jane.doe@example.com"
_AWS = "AKIAIOSFODNN7EXAMPLE"


def _pydantic_tool(name: str = "get_weather") -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "Get the weather for a city",
            "strict": True,
            "parameters": {
                "$defs": {"Unit": {"enum": ["celsius", "fahrenheit"], "title": "Unit",
                                   "type": "string"}},
                "properties": {
                    "city": {"description": "City name", "title": "City", "type": "string"},
                    "unit": {"anyOf": [{"$ref": "#/$defs/Unit"}, {"type": "null"}],
                             "default": None},
                    "tags": {"items": {"type": "string"}, "type": "array", "default": ["a", "b"]},
                },
                "required": ["city"],
                "title": "WeatherArgs",
                "type": "object",
                "additionalProperties": False,
            },
        },
    }


def _ok(extra: dict[str, Any], messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    # a JSON-mode request needs JSON content back (D9); anything else gets plain text
    provider = _Recorder(reply="{}" if "response_format" in extra else "ok")
    status, body = _chat(provider, {"messages": messages or [_USER], **extra})
    assert status == 200, body
    [sent] = provider.sent
    return dict(sent)


def _blocked(extra: dict[str, Any], messages: list[dict[str, Any]] | None = None) -> bytes:
    provider = _Recorder()
    status, body = _chat(provider, {"messages": messages or [_USER], **extra})
    assert status == 400, (status, body)
    assert provider.sent == [], "a refused request must make zero provider calls"
    return body


def test_a_realistic_tools_request_is_forwarded_with_its_structure_intact() -> None:
    tools = [_pydantic_tool(), _pydantic_tool("search")]
    sent = _ok({
        "tools": tools,
        "tool_choice": {"type": "function", "function": {"name": "search"}},
        "parallel_tool_calls": False,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "answer", "description": "The answer", "strict": True,
            "schema": _pydantic_tool()["function"]["parameters"]}},
    })
    assert sent["tools"] == tools
    assert sent["tool_choice"] == {"type": "function", "function": {"name": "search"}}
    assert sent["parallel_tool_calls"] is False
    assert sent["response_format"]["json_schema"]["schema"] == tools[0]["function"]["parameters"]
    assert sent["response_format"]["json_schema"]["description"] == "The answer"


@pytest.mark.parametrize("choice", ["none", "auto", "required"])
def test_tool_choice_literals_and_json_object_are_forwarded(choice: str) -> None:
    sent = _ok({"tools": [_pydantic_tool()], "tool_choice": choice,
                "response_format": {"type": "json_object"}})
    assert sent["tool_choice"] == choice and sent["response_format"] == {"type": "json_object"}


def test_a_tool_round_trip_history_is_forwarded_with_canonical_arguments() -> None:
    messages = [
        _USER,
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_abc123", "type": "function",
             "function": {"name": "get_weather", "arguments": '{"city":  "Paris",\n "n": 1}'}}]},
        {"role": "tool", "tool_call_id": "call_abc123", "content": "sunny"},
        {"role": "assistant", "content": "It is sunny."},
    ]
    sent = _ok({"tools": [_pydantic_tool()]}, messages)
    assert sent["messages"][1] == {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "call_abc123", "type": "function",
                        "function": {"name": "get_weather",
                                     "arguments": '{"city":"Paris","n":1}'}}]}
    assert sent["messages"][2] == {"role": "tool", "content": "sunny",
                                   "tool_call_id": "call_abc123"}


def test_free_text_is_redacted_in_place_and_structure_is_preserved() -> None:
    tool = _pydantic_tool()
    tool["function"]["description"] = f"Look up {SSN} records"
    tool["function"]["parameters"]["properties"]["city"]["description"] = f"mail {_EMAIL} now"
    tool["function"]["parameters"]["properties"]["tags"]["default"] = [f"ssn {SSN}", "ok"]
    messages = [
        _USER,
        {"role": "assistant", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": json.dumps({"ssn": SSN})}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": f"found {SSN}"},
    ]
    sent = _ok({"tools": [tool]}, messages)
    wire = json.dumps(sent)
    assert SSN not in wire and _EMAIL not in wire
    function = sent["tools"][0]["function"]
    assert function["description"] == "Look up [REDACTED] records"
    assert function["parameters"]["properties"]["tags"]["default"] == ["ssn [REDACTED]", "ok"]
    args = sent["messages"][1]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(args) == {"ssn": "[REDACTED]"}
    assert sent["messages"][2]["content"] == "found [REDACTED]"
    # structure is untouched
    assert function["parameters"]["required"] == ["city"]
    assert function["parameters"]["$defs"]["Unit"]["enum"] == ["celsius", "fahrenheit"]


def test_p3_a_secret_in_a_name_or_id_is_blocked_with_zero_provider_calls() -> None:
    # Feature: gw34-sdk-compat, Property 3: no unscanned byte reaches the provider
    history = [
        _USER,
        {"role": "assistant", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": "{}"}}]},
    ]
    cases: list[tuple[dict[str, Any], list[dict[str, Any]] | None]] = [
        ({"tools": [_pydantic_tool(_AWS)]}, None),                                  # tool name
        ({"tools": [_pydantic_tool(_AWS)],
          "tool_choice": {"type": "function", "function": {"name": _AWS}}}, None),  # choice name
        ({"tools": [_pydantic_tool()]}, [_USER, {"role": "assistant", "tool_calls": [
            {"id": _AWS, "type": "function",
             "function": {"name": "get_weather", "arguments": "{}"}}]}]),          # call id
        ({"tools": [_pydantic_tool()]}, [*history, {"role": "tool", "tool_call_id": "call_1",
                                                    "content": "ok"}]),             # control ok
    ]
    for extra, messages in cases[:3]:
        body = _blocked(extra, messages)
        assert json.loads(body)["error"]["code"] == "input_blocked", body
    _ok(*cases[3])  # the control is accepted, so the blocks above are attributable


def test_p3_a_secret_in_a_tool_call_id_of_a_tool_message_is_blocked() -> None:
    messages = [
        _USER,
        {"role": "assistant", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]
    # the id tie requires the tool message to reuse the (scanned) assistant id; a secret id on
    # BOTH is blocked once, with zero provider calls
    messages[1]["tool_calls"][0]["id"] = _AWS
    messages[2]["tool_call_id"] = _AWS
    body = _blocked({"tools": [_pydantic_tool()]}, messages)
    assert json.loads(body)["error"]["code"] == "input_blocked"


@pytest.mark.parametrize("where", ["property", "defs_key", "required", "enum"])
def test_p3_a_redact_class_value_in_a_structural_position_blocks(where: str) -> None:
    tool = _pydantic_tool()
    params = tool["function"]["parameters"]
    if where == "property":
        params["properties"][_EMAIL] = {"type": "string"}
    elif where == "defs_key":
        params["$defs"][_EMAIL] = {"type": "string"}
    elif where == "required":
        params["required"] = [_EMAIL]
    else:
        params["properties"]["city"]["enum"] = [_EMAIL, "b"]
    body = _blocked({"tools": [tool]})
    assert json.loads(body)["error"]["code"] == "input_blocked"


def test_the_same_email_in_a_parameter_description_is_redacted_not_blocked() -> None:
    tool = _pydantic_tool()
    tool["function"]["parameters"]["properties"]["city"]["description"] = f"mail {_EMAIL}"
    sent = _ok({"tools": [tool]})
    assert _EMAIL not in json.dumps(sent)


def test_p4_redaction_never_forwards_invalid_json_arguments() -> None:
    # Feature: gw34-sdk-compat, Property 4 (request side): arguments stay valid JSON or refuse
    rng = random.Random(0x6A34_0104)
    pieces = [SSN, _EMAIL, "plain", "x y z", "é🙂", '"quoted"']
    for _ in range(100):
        value = {
            "".join(rng.choice("abc") for _ in range(3)): [rng.choice(pieces) for _ in range(3)],
            "k": {"n": rng.choice(pieces)},
        }
        messages = [
            _USER,
            {"role": "assistant", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "get_weather", "arguments": json.dumps(value)}}]},
        ]
        provider = _Recorder()
        status, body = _chat(provider, {"tools": [_pydantic_tool()], "messages": messages})
        if status == 200:
            [sent] = provider.sent
            arguments = sent["messages"][1]["tool_calls"][0]["function"]["arguments"]
            json.loads(arguments)
            assert SSN not in arguments and _EMAIL not in arguments
        else:
            assert status == 400 and provider.sent == [], body


def test_realistic_ids_and_names_are_not_false_positives_on_block_only_channels() -> None:
    rng = random.Random(7)
    alnum = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    ids = [
        str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        "call_" + "".join(rng.choice(alnum) for _ in range(24)),
        "toolu_" + "".join(rng.choice(alnum) for _ in range(22)),
        "chatcmpl-tool-" + "".join(rng.choice("0123456789abcdef") for _ in range(32)),
    ]
    names = ["get_current_weather", "searchWeb", "lookup-user_by_id", "calc"]
    for call_id in ids:
        for name in names:
            messages = [
                _USER,
                {"role": "assistant", "tool_calls": [
                    {"id": call_id, "type": "function",
                     "function": {"name": name, "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": call_id, "content": "ok"},
            ]
            sent = _ok({"tools": [_pydantic_tool(name)]}, messages)
            assert sent["messages"][1]["tool_calls"][0]["id"] == call_id


def _scan_slots(n_tools: int) -> tuple[int, int]:
    """(slots used, max_scanned_slots) for a realistic ``n_tools`` request with history."""
    tools = [_pydantic_tool(f"tool_{i}") for i in range(n_tools)]
    messages: list[dict[str, Any]] = [_USER]
    for i in range(20):
        messages.append({"role": "assistant", "tool_calls": [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": "tool_0", "arguments": '{"a": 1}'}}]})
        messages.append({"role": "tool", "tool_call_id": f"call_{i}", "content": "r"})
    body = {"model": "m", "messages": messages, "tools": tools,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "r", "schema": _pydantic_tool()["function"]["parameters"]}}}
    contract = _contract()
    req = OpenAIRequestParser().parse(
        json.dumps(body).encode(), surface=Surface.CHAT, contract=contract
    )
    template = req.upstream_template
    assert template is not None
    return len(template.joined), contract.tool_bounds().max_scanned_slots


def test_twenty_and_forty_tool_requests_have_scan_slot_headroom() -> None:
    used20, cap = _scan_slots(20)
    assert used20 <= 0.7 * cap, (used20, cap)  # REQUIRED target
    used40, cap = _scan_slots(40)
    assert used40 <= 0.8 * cap, (used40, cap)  # stretch target (recorded in the exit note)


def _leaves(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in (*_leaves(k), *_leaves(v))]
    if isinstance(node, (list, tuple)):
        return [s for item in node for s in _leaves(item)]
    return []


def test_p3_every_string_leaf_of_the_rendered_body_is_accounted_for() -> None:
    # Feature: gw34-sdk-compat, Property 3: no byte reaches the provider that was not scanned
    from types import MappingProxyType

    from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY
    from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
    from gateway_v2.edge.upstream_body import with_openai_body
    from gateway_v2.edge.wire.openai_template import JOIN_SEPARATOR
    from gateway_v2.edge.wire.openai_tools import SCHEMA_KEYWORDS

    rng = random.Random(0x6A34_0003)
    closed = {"function", "object", "string", "number", "integer", "boolean", "array", "null",
              "none", "auto", "required", "text", "json_object", "json_schema", "assistant",
              "user", "system", "tool", "model", *SCHEMA_KEYWORDS, "name", "arguments", "id",
              "type", "role", "content", "messages", "tools", "tool_choice", "strict", "schema",
              "description", "parameters", "json_schema", "tool_calls", "tool_call_id", "stream",
              "max_tokens", "response_format", "parallel_tool_calls", "m"}
    contract = _contract()
    alphabet = "abcdefghij klmnop-_é"
    for _ in range(120):
        names = [f"fn_{rng.randint(0, 99999)}" for _ in range(rng.randint(1, 4))]
        tools = []
        for n in dict.fromkeys(names):
            tool = _pydantic_tool(n)
            tool["function"]["description"] = "".join(rng.choice(alphabet) for _ in range(12))
            tool["function"]["parameters"]["properties"][
                "".join(rng.choice("abcxyz") for _ in range(6))] = {
                "type": "string", "enum": ["".join(rng.choice("pqr") for _ in range(4))]}
            tools.append(tool)
        call_id = f"call_{rng.randint(0, 10**9)}"
        body = {"model": "m", "tools": tools, "messages": [
            _USER, {"role": "assistant", "tool_calls": [
                {"id": call_id, "type": "function",
                 "function": {"name": tools[0]["function"]["name"],
                              "arguments": json.dumps({"q": "".join(rng.choice(alphabet)
                                                                    for _ in range(8))})}}]},
            {"role": "tool", "tool_call_id": call_id, "content": "".join(
                rng.choice(alphabet) for _ in range(9))}]}
        req = OpenAIRequestParser().parse(json.dumps(body).encode(), surface=Surface.CHAT,
                                          contract=contract)
        rendered = with_openai_body(MappingProxyType({BODY_DELTAS_KEY: req.text_deltas}),
                                    req, contract, model=req.model,
                                    org_ceiling=contract.max_output_tokens())
        assert rendered is not None and not hasattr(rendered, "code")
        sent = rendered[BODY_OPENAI_KEY]
        verified = {part for _c, text in req.text_deltas for part in text.split(JOIN_SEPARATOR)}
        verified |= {text for _c, text in req.text_deltas}
        for leaf in _leaves(json.loads(json.dumps(sent, default=lambda o: dict(o)
                                                  if hasattr(o, "items") else list(o)))):
            assert leaf in verified or leaf in closed, leaf
