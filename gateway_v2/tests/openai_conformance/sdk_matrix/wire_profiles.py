"""Documented request shapes of the clients the runbook names, as a FROZEN table (D13).

The framework libraries are not installed (the live gate is deferred); these are the request
bodies and headers their documented wire formats produce. Each case is either served (200) or a
declared 400 that names the field; never a 5xx.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

__all__ = ["PROFILES", "WireProfile"]

_USER = {"role": "user", "content": "SDKMATRIX:plain"}
_PYDANTIC_SCHEMA = {
    "$defs": {"Unit": {"enum": ["c", "f"], "title": "Unit", "type": "string"}},
    "properties": {
        "city": {"title": "City", "type": "string"},
        "unit": {"anyOf": [{"$ref": "#/$defs/Unit"}, {"type": "null"}], "default": None},
    },
    "required": ["city"],
    "title": "WeatherArgs",
    "type": "object",
    "additionalProperties": False,
}
_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather", "description": "weather", "parameters": _PYDANTIC_SCHEMA,
    },
}
_ASSISTANT_CALL = {"role": "assistant", "content": None, "tool_calls": [
    {"id": "call_1", "type": "function",
     "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}}]}
_TOOL_RESULT = {"role": "tool", "tool_call_id": "call_1", "content": "sunny"}


@dataclass(frozen=True)
class WireProfile:
    client: str
    shape: str
    body: Any
    status: int
    error_param: str | None = None
    path: str = "/v1/chat/completions"
    headers: Any = field(default_factory=lambda: MappingProxyType({}))


def _body(**extra: Any) -> dict[str, Any]:
    return {"model": "gpt-4o-mini", "messages": [_USER], **extra}


PROFILES: tuple[WireProfile, ...] = (
    WireProfile("openai-sdk", "stream_options.include_usage", _body(
        stream=True, stream_options={"include_usage": True}), 200),
    WireProfile("openai-sdk", "tool_choice auto", _body(tools=[_TOOL], tool_choice="auto"), 200),
    WireProfile("openai-sdk", "tool_choice required", _body(
        tools=[_TOOL], tool_choice="required"), 200),
    WireProfile("openai-sdk", "tool_choice named", _body(
        tools=[_TOOL], tool_choice={"type": "function", "function": {"name": "get_weather"}}), 200),
    WireProfile("openai-sdk", "parallel_tool_calls false", _body(
        tools=[_TOOL], parallel_tool_calls=False), 200),
    WireProfile("langchain", "assistant+tool round trip", {
        "model": "gpt-4o-mini", "tools": [_TOOL], "temperature": 0.7, "n": 1,
        "messages": [_USER, _ASSISTANT_CALL, _TOOL_RESULT]}, 200),
    WireProfile("llamaindex", "text-part arrays", _body(messages=[
        {"role": "system", "content": [{"type": "text", "text": "be terse"}]},
        {"role": "user", "content": [{"type": "text", "text": "SDKMATRIX:plain"}]}]), 200),
    WireProfile("litellm", "user/metadata/seed/max_completion_tokens", _body(
        user="u-1", metadata={"k": "v"}, seed=7, max_completion_tokens=64), 200),
    WireProfile("vercel-ai-sdk", "pydantic-v2 json_schema response_format", _body(
        messages=[{"role": "user", "content": "SDKMATRIX:json"}],
        response_format={"type": "json_schema", "json_schema": {
            "name": "weather", "strict": True, "schema": _PYDANTIC_SCHEMA}}), 200),
    WireProfile("openai-sdk", "logprobs true is a declared 400", _body(logprobs=True), 400,
                error_param="logprobs"),
    WireProfile("openai-sdk", "image_url part is a declared 400", {
        "model": "gpt-4o-mini", "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "http://x/y.png"}}]}]}, 400,
                error_param="messages"),
    WireProfile("openai-sdk", "legacy functions is a declared 400", _body(functions=[]), 400,
                error_param="functions"),
    WireProfile("azure", "azure deployment path is a 404", _body(), 404,
                path="/openai/deployments/gpt4/chat/completions"),
    WireProfile("azure", "api-key only auth is a 401", _body(), 401,
                headers=MappingProxyType({"api-key": "sk-gw34-matrix"})),
)
