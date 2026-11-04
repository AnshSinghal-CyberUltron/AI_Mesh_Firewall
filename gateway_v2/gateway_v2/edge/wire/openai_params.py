"""What a chat/completions request may forward upstream (GW36 S1).

The gateway forwards only what it scans or can type-check. :func:`forwarded_fields` validates the
request and returns the top-level fields copied to the provider as given; anything the gateway
does not scan or type-check is refused with a declared 400 naming the field, never dropped
silently and never copied through:

* ``functions`` / ``function_call`` (deprecated), true ``logprobs`` / ``top_logprobs``, ``audio``,
  ``modalities``, ``prediction``, ``web_search_options`` and every other field outside
  :data:`_FORWARDED`, :data:`_ACCEPTED` and :data:`_TOOL_FIELDS` -> ``unsupported_parameter``;
* ``tools`` / ``tool_choice`` / ``parallel_tool_calls`` / ``response_format`` are handled by
  ``openai_tools`` (GW34) when the route has tool bounds; without them they are refused
  ``unsupported_parameter`` (a tools-disabled route);
* messages with role ``function`` or carrying ``function_call`` -> ``unsupported_parameter``
  (param ``messages``); role ``tool`` / assistant ``tool_calls`` need tool bounds, else the same;
* a content part that is not text (image, audio, file) -> ``unsupported_content_part``;
* a body with both ``messages`` and ``prompt``, a ``prompt`` that is not one string, or token ids
  -> ``unsupported_parameter`` (param ``prompt``).

``stop`` and ``prompt`` are text and are folded + scanned (``openai_fold``); a message ``name`` is
accepted but not forwarded (it is free text the scan does not cover). PURE wire logic.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from types import MappingProxyType

from gateway_v2.edge.wire.openai_codes import (
    MALFORMED_WRONG_SHAPE,
    UNSUPPORTED_CONTENT_PART,
    UNSUPPORTED_PARAMETER,
    MalformedRequest,
)
from gateway_v2.runtime.resources_tools import ToolBounds

__all__ = ("REFUSED_FIELDS", "accepted_fields", "forwarded_fields")


def accepted_fields() -> frozenset[str]:
    """Every top-level field the parser accepts (the complement is refused)."""
    return _FORWARDED | _ACCEPTED | _TOOL_FIELDS | _FALSEY_ONLY

#: Numeric sampling fields forwarded as given, each with its inclusive OpenAI range.
_RANGED = MappingProxyType(
    {
        "temperature": (0.0, 2.0),
        "top_p": (0.0, 1.0),
        "presence_penalty": (-2.0, 2.0),
        "frequency_penalty": (-2.0, 2.0),
    }
)
_REASONING_EFFORT = frozenset({"minimal", "low", "medium", "high"})
_LOGIT_BIAS_RANGE = (-100.0, 100.0)
#: OpenAI accepts at most this many ``stop`` sequences (a protocol shape, not a capacity bound).
_STOP_SEQUENCES_LIMIT = 4

#: Fields copied to the provider after validation.
_FORWARDED = frozenset((*_RANGED, "seed", "logit_bias", "reasoning_effort"))
#: Fields read elsewhere (model, messages, prompt, stop, the budget) or accepted and not forwarded.
_ACCEPTED = frozenset(
    {
        "model",
        "messages",
        "prompt",
        "stop",
        "max_tokens",
        "max_completion_tokens",
        "n",
        "stream",
        "stream_options",
        "user",
        "metadata",
        "store",
        "service_tier",
    }
)
#: Request-level tool fields: validated and folded by ``openai_tools`` (never copied through).
_TOOL_FIELDS = frozenset({"tools", "tool_choice", "parallel_tool_calls", "response_format"})
#: Accepted ONLY as ``false`` / ``null`` and never forwarded (the logprob arrays are a text
#: channel the output redactor cannot treat consistently with ``content``, D3).
_FALSEY_ONLY = frozenset({"logprobs", "top_logprobs"})
#: The explicit refusal set (property 8 partitions accepted vs refused over a field corpus):
#: anything not in ``_FORWARDED``/``_ACCEPTED``/``_TOOL_FIELDS``/``_FALSEY_ONLY`` is refused;
#: these are the named ones.
REFUSED_FIELDS = frozenset(
    {"functions", "function_call", "audio", "modalities", "prediction", "web_search_options"}
)
_ROLES = frozenset({"system", "developer", "user", "assistant"})
_TOOL_ROLE = "tool"
_ASSISTANT_ROLE = "assistant"
_TEXT_PART_TYPES = frozenset({"text"})
_MODEL_PATTERN = re.compile(r"[A-Za-z0-9._:/-]{1,128}")
#: Only a field name of this shape is echoed back in ``param``; anything else echoes nothing.
_ECHOABLE_PARAM = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def forwarded_fields(
    obj: Mapping[str, object], bounds: ToolBounds | None = None
) -> Mapping[str, object]:
    """Validate ``obj`` for forwarding and return the validated fields copied as given.

    ``bounds`` is the route's tool bounds; ``None`` is a tools-disabled route (tool-bearing
    requests are refused ``unsupported_parameter``, fail closed).
    """
    for key in obj:
        if key not in accepted_fields():
            raise MalformedRequest(UNSUPPORTED_PARAMETER, param=_echo(key))
    for key in _FALSEY_ONLY:
        if obj.get(key) not in (None, False) or (key == "top_logprobs" and obj.get(key) is False):
            raise MalformedRequest(UNSUPPORTED_PARAMETER, param=key)
    if bounds is None:
        for key in _TOOL_FIELDS:
            if obj.get(key) is not None:
                raise MalformedRequest(UNSUPPORTED_PARAMETER, param=key)
    _check_model(obj.get("model"))
    _check_messages(obj.get("messages"), tools_enabled=bounds is not None)
    _check_prompt(obj)
    _check_stop(obj.get("stop"))
    forwarded: dict[str, object] = {}
    for key, (low, high) in _RANGED.items():
        if obj.get(key) is not None:
            forwarded[key] = _number(obj[key], key, low, high)
    if obj.get("seed") is not None:
        forwarded["seed"] = _integer(obj["seed"], "seed")
    if obj.get("reasoning_effort") is not None:
        if obj["reasoning_effort"] not in _REASONING_EFFORT:
            raise MalformedRequest(MALFORMED_WRONG_SHAPE, param="reasoning_effort")
        forwarded["reasoning_effort"] = obj["reasoning_effort"]
    if obj.get("logit_bias") is not None:
        forwarded["logit_bias"] = _logit_bias(obj["logit_bias"])
    return MappingProxyType(forwarded)


def _echo(key: str) -> str | None:
    return key if _ECHOABLE_PARAM.fullmatch(key) else None


def _check_model(model: object) -> None:
    if isinstance(model, str) and not _MODEL_PATTERN.fullmatch(model):
        raise MalformedRequest(MALFORMED_WRONG_SHAPE, param="model")


def _check_messages(messages: object, *, tools_enabled: bool) -> None:
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if message.get("function_call"):
            raise MalformedRequest(UNSUPPORTED_PARAMETER, param="messages")
        tool_turn = role == _TOOL_ROLE or (role == _ASSISTANT_ROLE and message.get("tool_calls"))
        if tool_turn and not tools_enabled:
            raise MalformedRequest(UNSUPPORTED_PARAMETER, param="messages")
        if role not in _ROLES and role != _TOOL_ROLE:
            raise MalformedRequest(UNSUPPORTED_PARAMETER, param="messages")
        if message.get("tool_calls") and role != _ASSISTANT_ROLE:
            raise MalformedRequest(UNSUPPORTED_PARAMETER, param="messages")
        content = message.get("content")
        if content is None or isinstance(content, str):
            continue
        if not isinstance(content, list):
            raise MalformedRequest(MALFORMED_WRONG_SHAPE, param="messages")
        for part in content:
            if not isinstance(part, dict):
                raise MalformedRequest(MALFORMED_WRONG_SHAPE, param="messages")
            if part.get("type", "text") not in _TEXT_PART_TYPES:
                raise MalformedRequest(UNSUPPORTED_CONTENT_PART, param="messages")
            if not isinstance(part.get("text"), str):
                raise MalformedRequest(MALFORMED_WRONG_SHAPE, param="messages")


def _check_prompt(obj: Mapping[str, object]) -> None:
    if "prompt" not in obj:
        return
    prompt = obj["prompt"]
    if "messages" in obj:
        raise MalformedRequest(UNSUPPORTED_PARAMETER, param="prompt")
    single = isinstance(prompt, list) and len(prompt) == 1 and isinstance(prompt[0], str)
    if not (isinstance(prompt, str) or single):
        raise MalformedRequest(UNSUPPORTED_PARAMETER, param="prompt")


def _check_stop(stop: object) -> None:
    if stop is None or isinstance(stop, str):
        return
    if (
        not isinstance(stop, list)
        or len(stop) > _STOP_SEQUENCES_LIMIT
        or not all(isinstance(item, str) for item in stop)
    ):
        raise MalformedRequest(MALFORMED_WRONG_SHAPE, param="stop")


def _number(value: object, key: str, low: float, high: float) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MalformedRequest(MALFORMED_WRONG_SHAPE, param=key)
    if not math.isfinite(value) or not low <= value <= high:
        raise MalformedRequest(MALFORMED_WRONG_SHAPE, param=key)
    return value


def _integer(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedRequest(MALFORMED_WRONG_SHAPE, param=key)
    return value


def _logit_bias(value: object) -> Mapping[str, float | int]:
    if not isinstance(value, dict):
        raise MalformedRequest(MALFORMED_WRONG_SHAPE, param="logit_bias")
    low, high = _LOGIT_BIAS_RANGE
    bias: dict[str, float | int] = {}
    for token, weight in value.items():
        if not (isinstance(token, str) and token.isascii() and token.isdigit()):
            raise MalformedRequest(MALFORMED_WRONG_SHAPE, param="logit_bias")
        bias[token] = _number(weight, "logit_bias", low, high)
    return MappingProxyType(bias)
