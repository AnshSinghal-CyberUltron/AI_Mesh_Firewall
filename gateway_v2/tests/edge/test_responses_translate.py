# Feature: gw15-responses-close, Property 1: Every text channel is present in the chat messages
# Feature: gw15-responses-close, Property 2: A part outside the translated set refuses
# Validates: Requirements 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 4.2, 4.4, 4.5, 7.1, 7.5
"""Translator proofs for the Responses wire adapter.

House idiom: a seeded ``random.Random``, no ``hypothesis``, at least 10,000 iterations.
"""

from __future__ import annotations

import json
import random
from collections.abc import Mapping

from gateway_v2.edge.wire.responses import (
    RESPONSES_INVALID_MODEL,
    RESPONSES_MALFORMED_BODY,
    RESPONSES_MISSING_INPUT,
    UNSUPPORTED_CONTENT_PART,
    ChatBody,
    TranslateRefusal,
    json_ready,
    response_object,
    translate_request,
)

_SEED = 0x6C15_0001
_ITERATIONS = 10_000
_CANARY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
_FIXED_UNSUPPORTED = ("input_file", "input_audio", "input_image")
_OTHER_UNSUPPORTED = ("refusal", "reasoning", "audio", "file", "image_url", "video_url")


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{key}={value!r}" for key, value in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _canary(rng: random.Random) -> str:
    n = rng.randint(4, 24)
    return "CANARY_" + "".join(rng.choice(_CANARY_ALPHABET) for _ in range(n))


def _contains(value: object, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, Mapping):
        return any(_contains(item, needle) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains(item, needle) for item in value)
    return False


def _body_for(channel: str, canary: str) -> dict[str, object]:
    """One Responses body that places ``canary`` in a single text channel."""
    if channel == "input":
        return {"model": "m", "input": canary}
    if channel == "text_part":
        return {
            "model": "m",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": canary}]}],
        }
    if channel == "instructions":
        return {"model": "m", "instructions": canary, "input": "hello"}
    if channel == "function_call":
        return {
            "model": "m",
            "input": [{"type": "function_call", "name": "lookup", "arguments": canary}],
        }
    return {
        "model": "m",
        "input": [{"type": "function_call_output", "call_id": "call_1", "output": canary}],
    }


def test_property_text_channels_survive_translation() -> None:
    """Property 1: a canary in each text channel appears in the chat messages.

    Validates: Requirements 2.1, 2.2, 2.3
    """
    rng = random.Random(_SEED)
    channels = ("input", "text_part", "instructions", "function_call", "function_call_output")
    for i in range(_ITERATIONS):
        channel = channels[i % len(channels)]
        canary = _canary(rng)
        result = translate_request(_body_for(channel, canary))
        assert isinstance(result, ChatBody), _ctx(_SEED, i, channel=channel, result=result)
        ready = json_ready(result.payload)
        assert _contains(ready, canary), _ctx(_SEED, i, channel=channel)


def _unsupported_type(rng: random.Random, i: int) -> str:
    if i % 5 < len(_FIXED_UNSUPPORTED):
        return _FIXED_UNSUPPORTED[i % len(_FIXED_UNSUPPORTED)]
    pool = _OTHER_UNSUPPORTED
    chosen = pool[rng.randrange(len(pool))]
    if chosen in {"input_text", "output_text", "text"}:
        return f"{chosen}_refused"
    return chosen


def test_property_unsupported_part_is_never_dropped() -> None:
    """Property 2: a part outside the translated set refuses and yields no chat body.

    Validates: Requirements 3.4
    """
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        kind = _unsupported_type(rng, i)
        body = {
            "model": "m",
            "input": [{"role": "user", "content": [{"type": kind, "text": "secret"}]}],
        }
        result = translate_request(body)
        assert isinstance(result, TranslateRefusal), _ctx(_SEED, i, kind=kind, result=result)
        assert result.code == UNSUPPORTED_CONTENT_PART, _ctx(_SEED, i, kind=kind, code=result.code)
        assert result.param == "input", _ctx(_SEED, i, kind=kind, param=result.param)
        assert not isinstance(result, ChatBody)


def test_malformed_body_refuses() -> None:
    """A non-object body is ``responses_malformed_body``. Validates: Requirements 3.1."""
    for body in (b"not-json", b"[]", b"null", b"123"):
        result = translate_request(body)
        assert isinstance(result, TranslateRefusal)
        assert result.code == RESPONSES_MALFORMED_BODY
        assert result.param is None


def test_invalid_model_sets_param() -> None:
    """A missing or non-string model names ``param`` ``model``. Validates: Requirements 3.2."""
    for body in ({"input": "hi"}, {"model": 123, "input": "hi"}, {"model": None, "input": "hi"}):
        result = translate_request(body)
        assert isinstance(result, TranslateRefusal)
        assert result.code == RESPONSES_INVALID_MODEL
        assert result.param == "model"


def test_missing_input_sets_param() -> None:
    """No input and no instructions names ``param`` ``input``. Validates: Requirements 3.3."""
    for body in ({"model": "m"}, {"model": "m", "instructions": "   "}):
        result = translate_request(body)
        assert isinstance(result, TranslateRefusal)
        assert result.code == RESPONSES_MISSING_INPUT
        assert result.param == "input"


def test_replay_sits_between_instructions_and_the_user_turn() -> None:
    """Replay messages follow the system message and precede the new user text."""
    result = translate_request(
        {"model": "m", "instructions": "SYS", "input": "USER"},
        ({"role": "assistant", "content": "PRIOR", "evidence": "secret"},),
    )
    assert isinstance(result, ChatBody)
    messages = json_ready(result.payload)["messages"]
    assert [item["role"] for item in messages] == ["system", "assistant", "user"]
    assert [item["content"] for item in messages] == ["SYS", "PRIOR", "USER"]
    assert "evidence" not in messages[1]


def test_budget_and_text_format_pass_through() -> None:
    """``max_output_tokens`` becomes ``max_tokens``. An explicit response_format wins."""
    mapped = translate_request(
        {
            "model": "m",
            "input": "hi",
            "max_output_tokens": 16,
            "temperature": 0.2,
            "text": {"format": {"type": "json_object"}},
        }
    )
    assert isinstance(mapped, ChatBody)
    payload = json_ready(mapped.payload)
    assert payload["max_tokens"] == 16
    assert payload["temperature"] == 0.2
    assert payload["response_format"] == {"type": "json_object"}
    explicit = translate_request(
        {
            "model": "m",
            "input": "hi",
            "response_format": {"type": "text"},
            "text": {"format": {"type": "json_object"}},
        }
    )
    assert isinstance(explicit, ChatBody)
    assert json_ready(explicit.payload)["response_format"] == {"type": "text"}
    completion_tokens = translate_request(
        {"model": "m", "input": "hi", "max_completion_tokens": 32}
    )
    assert isinstance(completion_tokens, ChatBody)
    ready = json_ready(completion_tokens.payload)
    assert ready["max_completion_tokens"] == 32
    assert "max_tokens" not in ready


def test_mcp_item_text_is_placed_in_a_tool_message() -> None:
    """An MCP item's payload is a tool message, not a dropped field."""
    result = translate_request(
        {"model": "m", "input": [{"type": "mcp_call", "name": "search", "arguments": "CANARY_MCP"}]}
    )
    assert isinstance(result, ChatBody)
    messages = json_ready(result.payload)["messages"]
    assert messages[0]["role"] == "tool"
    assert messages[0]["content"] == "CANARY_MCP"


def test_response_object_keeps_redacted_text_and_drops_evidence() -> None:
    """The response carries the enforced text, the console object, and no evidence.

    Validates: Requirements 4.2, 4.4, 4.5, 7.1, 7.5
    """
    raw = "4111111111111111"
    redacted = "[CARD]"
    completion = {
        "model": "upstream-secret-id",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": redacted,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{\"q\":\"x\"}"},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "zeroshield": {"action": "redact", "evidence": raw},
        "pipeline_trace": {"stages": [{"name": "output_guardrail", "evidence": raw}]},
    }
    obj = response_object(completion, model="gpt-4o-mini", store=False)
    assert str(obj["id"]).startswith("resp_")
    assert obj["object"] == "response"
    assert obj["status"] == "completed"
    assert obj["model"] == "gpt-4o-mini"
    assert obj["output_text"] == redacted
    assert raw not in json.dumps(obj)
    assert "upstream-secret-id" not in json.dumps(obj)
    usage = obj["usage"]
    assert isinstance(usage, dict)
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 5
    assert usage["total_tokens"] == 15
    zeroshield = obj["zeroshield"]
    assert isinstance(zeroshield, dict)
    assert zeroshield["action"] == "redact"
    assert "evidence" not in zeroshield
    trace = obj["pipeline_trace"]
    assert isinstance(trace, dict)
    stages = trace["stages"]
    assert isinstance(stages, list)
    assert "evidence" not in stages[0]
    output = obj["output"]
    assert isinstance(output, list)
    assert output[1]["type"] == "function_call"
    assert output[1]["arguments"] == "{\"q\":\"x\"}"
