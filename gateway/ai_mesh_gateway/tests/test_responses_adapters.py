"""Unit tests for the OpenAI Responses<->Chat format adapters (pure functions)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from responses_adapters import (  # noqa: E402
    generate_openai_id, build_openai_error, coerce_chat_error_to_openai,
    responses_to_chat, chat_completion_to_responses,
)


def test_id_prefixes():
    assert generate_openai_id("response").startswith("resp_")
    assert generate_openai_id("message").startswith("msg_")
    assert generate_openai_id("function_call").startswith("fc_")


def test_error_envelope_is_nested_openai_shape():
    e = build_openai_error(403, "blocked", code="content_blocked")
    assert e["error"]["message"] == "blocked"
    assert e["error"]["type"] == "permission_error"  # ZS code -> OpenAI type
    e2 = build_openai_error(429, "slow down")
    assert e2["error"]["type"] == "rate_limit_error"


def test_coerce_flat_chat_error_to_nested_keeps_diagnostics():
    flat = {"error": "blocked", "message": "Request blocked", "code": "content_blocked",
            "request_id": "zs-1", "pipeline_trace": {"x": 1}}
    out = coerce_chat_error_to_openai(403, flat)
    assert out["error"]["message"] == "Request blocked"
    assert out["error"]["type"] == "permission_error"
    assert out["request_id"] == "zs-1"          # ZS diagnostics stay top-level
    assert out["pipeline_trace"] == {"x": 1}


def test_responses_to_chat_string_input():
    chat = responses_to_chat({"model": "m", "input": "hello", "instructions": "be terse",
                              "max_output_tokens": 7, "temperature": 0.2})
    assert chat["model"] == "m"
    assert chat["messages"][0] == {"role": "system", "content": "be terse"}
    assert chat["messages"][1] == {"role": "user", "content": "hello"}
    assert chat["max_tokens"] == 7
    assert chat["temperature"] == 0.2


def test_responses_to_chat_typed_items_and_tool_output():
    body = {"model": "m", "input": [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "function_call_output", "call_id": "c1", "output": "42"},
    ]}
    chat = responses_to_chat(body)
    assert chat["messages"][0] == {"role": "user", "content": "hi"}
    assert chat["messages"][1] == {"role": "tool", "tool_call_id": "c1", "content": "42"}


def test_responses_to_chat_prepends_prior_messages():
    chat = responses_to_chat({"model": "m", "input": "next"},
                             prior_messages=[{"role": "user", "content": "prev"},
                                             {"role": "assistant", "content": "ok"}])
    roles = [m["role"] for m in chat["messages"]]
    assert roles == ["user", "assistant", "user"]


def test_chat_completion_to_responses_shape():
    completion = {"id": "chatcmpl-1", "created": 123, "model": "m",
                  "choices": [{"message": {"role": "assistant", "content": "hi there"},
                               "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
                  "zeroshield": {"action": "allow"}}
    r = chat_completion_to_responses(completion, response_id="resp_x", model="m", store=True)
    assert r["object"] == "response"
    assert r["status"] == "completed"
    assert r["output_text"] == "hi there"
    assert r["output"][0]["type"] == "message"
    assert r["output"][0]["content"][0] == {"type": "output_text", "text": "hi there", "annotations": []}
    assert r["usage"] == {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7,
                          "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 0}}
    assert r["zeroshield"] == {"action": "allow"}  # trace carried through


def test_chat_completion_to_responses_length_is_incomplete():
    completion = {"choices": [{"message": {"content": "x"}, "finish_reason": "length"}], "usage": {}}
    r = chat_completion_to_responses(completion, response_id="resp_y", model="m")
    assert r["status"] == "incomplete"
    assert r["incomplete_details"] == {"reason": "max_output_tokens"}


def test_chat_completion_to_responses_tool_calls():
    completion = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        "finish_reason": "tool_calls"}], "usage": {}}
    r = chat_completion_to_responses(completion, response_id="resp_z", model="m")
    fc = [o for o in r["output"] if o["type"] == "function_call"]
    assert fc and fc[0]["name"] == "f" and fc[0]["call_id"] == "call_1"
