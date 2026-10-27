"""Responses API wire translation (GW15 close-out, task 2.1).

Pure format adapter. A Responses body becomes a chat-completions body, and a
chat completion becomes a Responses object. This module does not scan, resolve
a plan, admit, or call a provider. Those stay in the shipped chat lifecycle.

No capacity literal and no module-level mutable. The passthrough names and the
part-type sets are immutable.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

__all__ = (
    "RESPONSES_INVALID_MODEL",
    "RESPONSES_MALFORMED_BODY",
    "RESPONSES_MISSING_INPUT",
    "UNSUPPORTED_CONTENT_PART",
    "ChatBody",
    "TranslateRefusal",
    "json_ready",
    "new_id",
    "response_object",
    "translate_request",
)

#: The body is not a JSON object.
RESPONSES_MALFORMED_BODY = "responses_malformed_body"
#: ``model`` is missing or is not a string.
RESPONSES_INVALID_MODEL = "responses_invalid_model"
#: Neither ``input`` nor ``instructions`` carries text.
RESPONSES_MISSING_INPUT = "responses_missing_input"
#: A content part cannot be placed in a scanned chat text channel.
UNSUPPORTED_CONTENT_PART = "unsupported_content_part"

_PARAM_MODEL = "model"
_PARAM_INPUT = "input"

_TEXT_PART_TYPES = frozenset({"input_text", "output_text", "text"})
_MCP_ITEM_TYPES = frozenset(
    {
        "mcp_call",
        "mcp_approval_request",
        "mcp_approval_response",
        "mcp_list_tools",
        "custom_tool_call_output",
    }
)
_PASSTHROUGH = (
    "temperature",
    "top_p",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "stop",
    "seed",
    "user",
    "metadata",
    "stream",
    "response_format",
    "n",
)
_MESSAGE_ITEM_TYPES = frozenset({"message"})
_ID_HEX_BYTES = 24


@dataclass(frozen=True, slots=True)
class TranslateRefusal:
    """A body this adapter will not turn into a chat request."""

    code: str
    param: str | None = None


@dataclass(frozen=True, slots=True)
class ChatBody:
    """A chat-completions request. ``payload`` is frozen and JSON-ready via :func:`json_ready`."""

    payload: Mapping[str, object]


def translate_request(
    body: bytes | Mapping[str, object],
    replay_messages: Sequence[object] = (),
) -> ChatBody | TranslateRefusal:
    """Translate a Responses body into a chat request, or refuse.

    ``replay_messages`` are prepended after the system message and before the new
    user turn. This function does not read a store. A part it cannot place in a
    scanned text channel refuses the whole body.
    """
    decoded = _decode(body)
    if isinstance(decoded, TranslateRefusal):
        return decoded
    model = _model(decoded)
    if isinstance(model, TranslateRefusal):
        return model
    if not _has_text(decoded):
        return TranslateRefusal(RESPONSES_MISSING_INPUT, _PARAM_INPUT)
    messages = _messages(decoded, replay_messages)
    if isinstance(messages, TranslateRefusal):
        return messages
    payload: dict[str, object] = {"model": model, "messages": messages}
    _apply_budget(decoded, payload)
    _apply_passthrough(decoded, payload)
    _apply_text_format(decoded, payload)
    frozen = _freeze_object(payload)
    return ChatBody(payload=frozen)


def response_object(
    completion: Mapping[str, object],
    *,
    model: str,
    store: bool = False,
) -> dict[str, object]:
    """Build a Responses object from an enforced chat completion.

    ``model`` is the caller's model, not the upstream id. ``zeroshield`` and
    ``pipeline_trace`` are copied when present, with every ``evidence`` key removed.
    """
    message = _assistant_message(completion)
    text = message.get("content")
    output_text = text if isinstance(text, str) else ""
    obj: dict[str, object] = {
        "id": new_id("resp"),
        "object": "response",
        "status": "completed",
        "model": model,
        "output": _output_items(output_text, message),
        "output_text": output_text,
        "store": store,
    }
    usage = _usage(completion)
    if usage is not None:
        obj["usage"] = usage
    _copy_console(completion, obj)
    return obj


def json_ready(value: object) -> object:
    """Deep-copy a frozen payload into dicts and lists ``json.dumps`` accepts."""
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    return value


def _decode(body: bytes | Mapping[str, object]) -> Mapping[str, object] | TranslateRefusal:
    """Return the JSON object, or a malformed-body refusal."""
    if isinstance(body, Mapping):
        return body
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return TranslateRefusal(RESPONSES_MALFORMED_BODY)
    if not isinstance(decoded, dict):
        return TranslateRefusal(RESPONSES_MALFORMED_BODY)
    return decoded


def _model(body: Mapping[str, object]) -> str | TranslateRefusal:
    """The ``model`` string, or an invalid-model refusal."""
    model = body.get("model")
    if not isinstance(model, str):
        return TranslateRefusal(RESPONSES_INVALID_MODEL, _PARAM_MODEL)
    return model


def _has_text(body: Mapping[str, object]) -> bool:
    """True when ``input`` is present or ``instructions`` has non-blank text."""
    if "input" in body and body.get("input") is not None:
        return True
    instructions = body.get("instructions")
    return isinstance(instructions, str) and bool(instructions.strip())


def _messages(
    body: Mapping[str, object],
    replay_messages: Sequence[object],
) -> list[dict[str, object]] | TranslateRefusal:
    """System, then replay, then the new input items."""
    messages: list[dict[str, object]] = []
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    elif "instructions" in body and instructions is not None and not isinstance(instructions, str):
        return TranslateRefusal(RESPONSES_MALFORMED_BODY)
    replay = _replay(replay_messages)
    if isinstance(replay, TranslateRefusal):
        return replay
    messages.extend(replay)
    incoming = _input_messages(body.get("input"))
    if isinstance(incoming, TranslateRefusal):
        return incoming
    messages.extend(incoming)
    return messages


def _replay(replay_messages: Sequence[object]) -> list[dict[str, object]] | TranslateRefusal:
    """Copy caller-supplied replay turns, dropping any ``evidence`` key."""
    copied: list[dict[str, object]] = []
    for message in replay_messages:
        if not isinstance(message, Mapping):
            return TranslateRefusal(RESPONSES_MALFORMED_BODY)
        copied.append({str(key): value for key, value in message.items() if key != "evidence"})
    return copied


def _input_messages(raw: object) -> list[dict[str, object]] | TranslateRefusal:
    """Map ``input`` to chat messages. ``None`` adds nothing."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [{"role": "user", "content": raw}]
    if isinstance(raw, list):
        return _item_list(raw)
    return TranslateRefusal(RESPONSES_MALFORMED_BODY)


def _item_list(items: list[object]) -> list[dict[str, object]] | TranslateRefusal:
    """Map each input item, refusing the list on the first unsupported part."""
    messages: list[dict[str, object]] = []
    for item in items:
        mapped = _one_item(item)
        if isinstance(mapped, TranslateRefusal):
            return mapped
        messages.append(mapped)
    return messages


def _one_item(item: object) -> dict[str, object] | TranslateRefusal:
    """Map one input item onto one chat message."""
    if isinstance(item, str):
        return {"role": "user", "content": item}
    if not isinstance(item, Mapping):
        return TranslateRefusal(RESPONSES_MALFORMED_BODY)
    kind = item.get("type")
    if kind == "function_call":
        return _function_call_item(item)
    if kind == "function_call_output" or kind in _MCP_ITEM_TYPES:
        return _tool_item(item)
    if kind is None or kind in _MESSAGE_ITEM_TYPES:
        return _message_item(item)
    return TranslateRefusal(UNSUPPORTED_CONTENT_PART, _PARAM_INPUT)


def _function_call_item(item: Mapping[str, object]) -> dict[str, object]:
    """An input ``function_call`` becomes an assistant tool call."""
    call_id = item.get("call_id") if isinstance(item.get("call_id"), str) else item.get("id")
    name = item.get("name")
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id if isinstance(call_id, str) else "",
                "type": "function",
                "function": {
                    "name": name if isinstance(name, str) else "",
                    "arguments": _stringify(item.get("arguments")),
                },
            }
        ],
    }


def _tool_item(item: Mapping[str, object]) -> dict[str, object]:
    """A tool result or MCP item becomes a tool message whose content is scanned."""
    payload = item.get("output")
    if payload is None:
        payload = item.get("arguments")
    if payload is None:
        payload = {
            str(key): value
            for key, value in item.items()
            if key not in {"type", "id", "call_id", "status"}
        }
    call_id = item.get("call_id") if isinstance(item.get("call_id"), str) else item.get("id")
    return {
        "role": "tool",
        "tool_call_id": call_id if isinstance(call_id, str) else "",
        "content": _stringify(payload),
    }


def _message_item(item: Mapping[str, object]) -> dict[str, object] | TranslateRefusal:
    """A role/content message. A non-text part refuses the request."""
    role = item.get("role")
    if role is not None and not isinstance(role, str):
        return TranslateRefusal(RESPONSES_MALFORMED_BODY)
    content = item.get("content")
    if isinstance(content, str):
        return {"role": role or "user", "content": content}
    if isinstance(content, list):
        return _parts_message(role or "user", content)
    text = item.get("text")
    if isinstance(text, str):
        return {"role": role or "user", "content": text}
    return {"role": role or "user", "content": ""}


def _parts_message(role: str, parts: list[object]) -> dict[str, object] | TranslateRefusal:
    """Collapse text parts. One part stays a string. Several stay a text-part list."""
    texts: list[str] = []
    for part in parts:
        extracted = _text_part(part)
        if isinstance(extracted, TranslateRefusal):
            return extracted
        texts.append(extracted)
    if len(texts) == 1:
        return {"role": role, "content": texts[0]}
    return {"role": role, "content": [{"type": "text", "text": text} for text in texts]}


def _text_part(part: object) -> str | TranslateRefusal:
    """The text of one content part, or a refusal when the type is not scanned text."""
    if not isinstance(part, Mapping):
        return TranslateRefusal(UNSUPPORTED_CONTENT_PART, _PARAM_INPUT)
    kind = part.get("type")
    if kind not in _TEXT_PART_TYPES:
        return TranslateRefusal(UNSUPPORTED_CONTENT_PART, _PARAM_INPUT)
    text = part.get("text")
    if not isinstance(text, str):
        return TranslateRefusal(UNSUPPORTED_CONTENT_PART, _PARAM_INPUT)
    return text


def _apply_budget(body: Mapping[str, object], payload: dict[str, object]) -> None:
    """``max_output_tokens`` becomes ``max_tokens``. The other field is copied as itself."""
    if body.get("max_output_tokens") is not None:
        payload["max_tokens"] = body.get("max_output_tokens")
        return
    if body.get("max_completion_tokens") is not None:
        payload["max_completion_tokens"] = body.get("max_completion_tokens")


def _apply_passthrough(body: Mapping[str, object], payload: dict[str, object]) -> None:
    """Copy the allowlisted chat fields. Anything else is left behind."""
    for key in _PASSTHROUGH:
        value = body.get(key)
        if value is not None:
            payload[key] = value


def _apply_text_format(body: Mapping[str, object], payload: dict[str, object]) -> None:
    """Map ``text.format`` to ``response_format`` when the caller did not set one."""
    if "response_format" in payload:
        return
    text = body.get("text")
    if not isinstance(text, Mapping):
        return
    formatted = text.get("format")
    if isinstance(formatted, Mapping):
        payload["response_format"] = dict(formatted)


def _stringify(value: object) -> str:
    """A scanner-visible string. Non-strings are JSON, so a nested value is not dropped."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def _freeze_object(payload: dict[str, object]) -> Mapping[str, object]:
    """Freeze one object so the returned body shares no mutable container with the caller."""
    frozen = {key: _freeze_value(value) for key, value in payload.items()}
    return MappingProxyType(frozen)


def _freeze_value(value: object) -> object:
    """Recursively freeze dicts and lists. Leaves scalars as they are."""
    if isinstance(value, dict):
        return _freeze_object(value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def new_id(prefix: str) -> str:
    """``resp_`` / ``msg_`` / ``fc_`` plus 24 hex bytes. Not the gateway ``zs-`` request id."""
    return f"{prefix}_{secrets.token_hex(_ID_HEX_BYTES)}"


def _assistant_message(completion: Mapping[str, object]) -> Mapping[str, object]:
    """The first choice's message, or an empty mapping when the completion has none."""
    choices = completion.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    first = choices[0]
    if not isinstance(first, Mapping):
        return {}
    message = first.get("message")
    if isinstance(message, Mapping):
        return message
    return {}


def _output_items(output_text: str, message: Mapping[str, object]) -> list[dict[str, object]]:
    """The assistant text item, then one function-call item per tool call."""
    items: list[dict[str, object]] = []
    if output_text:
        items.append(
            {
                "type": "message",
                "id": new_id("msg"),
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": output_text, "annotations": []}],
            }
        )
    items.extend(_function_call_items(message))
    return items


def _function_call_items(message: Mapping[str, object]) -> list[dict[str, object]]:
    """Chat ``tool_calls`` become Responses ``function_call`` output items."""
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return []
    items: list[dict[str, object]] = []
    for call in calls:
        if not isinstance(call, Mapping):
            continue
        function = call.get("function")
        function_map = function if isinstance(function, Mapping) else {}
        name = function_map.get("name")
        arguments = function_map.get("arguments")
        call_id = call.get("id")
        items.append(
            {
                "type": "function_call",
                "id": new_id("fc"),
                "call_id": call_id if isinstance(call_id, str) else new_id("fc"),
                "name": name if isinstance(name, str) else "",
                "arguments": arguments if isinstance(arguments, str) else _stringify(arguments),
                "status": "completed",
            }
        )
    return items


def _usage(completion: Mapping[str, object]) -> dict[str, int] | None:
    """Chat token counts renamed to the Responses usage fields. Absent usage stays absent."""
    raw = completion.get("usage")
    if not isinstance(raw, Mapping):
        return None
    prompt = _int_field(raw, "prompt_tokens")
    completion_tokens = _int_field(raw, "completion_tokens")
    total_tokens = _int_field(raw, "total_tokens")
    if "total_tokens" not in raw:
        total_tokens = prompt + completion_tokens
    return {
        "input_tokens": prompt,
        "output_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _int_field(raw: Mapping[str, object], key: str) -> int:
    """A non-bool integer field, or 0 when the completion omitted it."""
    value = raw.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _copy_console(completion: Mapping[str, object], obj: dict[str, object]) -> None:
    """Copy the console object. An ``evidence`` key is removed at every depth."""
    for key in ("zeroshield", "pipeline_trace"):
        value = completion.get(key)
        if isinstance(value, Mapping):
            stripped = _strip_evidence(value)
            if isinstance(stripped, dict):
                obj[key] = stripped


def _strip_evidence(value: object) -> object:
    """Drop every key named ``evidence``. Other keys are copied."""
    if isinstance(value, Mapping):
        return {
            str(key): _strip_evidence(item) for key, item in value.items() if key != "evidence"
        }
    if isinstance(value, list):
        return [_strip_evidence(item) for item in value]
    if isinstance(value, tuple):
        return [_strip_evidence(item) for item in value]
    return value
