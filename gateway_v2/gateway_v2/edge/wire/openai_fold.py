"""Input-channel fold + upstream template for the OpenAI chat/completions surface (GW15, GW36 S1).

:func:`fold_request` walks a parsed request ONCE and produces two things from the same loop:

* the :class:`FoldResult` -- every scanned ``(channel_name, text)`` occurrence in document order
  plus each occurrence's ``messages[]`` index (what the input scan reads), and
* the :class:`UpstreamTemplate` -- the request structure the provider receives, where every
  scanned text occurrence is a :class:`TextSlot` placeholder. Raw text never enters the template;
  :mod:`gateway_v2.edge.upstream_body` fills slot ``i`` with VERIFIED (masked) delta ``i``.

Because one loop builds both, slot ``i`` is always fold pair ``i`` (same channel, same order).

The fold is TOTAL over the input-scoped ``TEXT_CHANNELS`` (C21): the set of channel names it can
emit must equal the set of ``TEXT_CHANNELS`` whose ``sdk_path`` starts with one of
:data:`INPUT_SDK_PATH_PREFIXES`; any divergence raises :class:`ChannelFoldError` at import.

Paths folded: ``messages[].content`` (string), ``messages[].content[].text`` (parts),
``messages[].tool_calls[]`` (id, name, raw + canonical arguments), ``messages[].tool_call_id``,
``tools[]`` / ``tool_choice`` / ``response_format`` (GW34, joined runs: see ``openai_template``),
``messages[].function_call.arguments``, the legacy completions ``prompt`` (string or list of
strings) and ``stop`` (string or list of strings).
PURE: no I/O, no security decision; the forwarding policy lives in
:mod:`gateway_v2.edge.wire.openai_params`.
"""

from __future__ import annotations

from collections.abc import Mapping

from gateway_v2.domain.channels import TEXT_CHANNELS
from gateway_v2.edge.wire.openai_codes import MALFORMED_WRONG_SHAPE, MalformedRequest
from gateway_v2.edge.wire.openai_template import (
    Builder,
    ContentTemplate,
    FoldResult,
    TemplateMessage,
    TemplateToolCall,
    TextSlot,
    ToolsTemplate,
    UpstreamTemplate,
)
from gateway_v2.edge.wire.openai_tools import fold_tool_calls, fold_tools, validate_tool_messages
from gateway_v2.runtime.resources_tools import ToolBounds

__all__ = (
    "INPUT_SDK_PATH_PREFIXES",
    "ChannelFoldError",
    "FoldResult",
    "TemplateMessage",
    "TemplateToolCall",
    "TextSlot",
    "ToolsTemplate",
    "UpstreamTemplate",
    "fold_input_channels",
    "fold_request",
)

#: The ``TextChannel.sdk_path`` prefixes that mark a chat-input (request-side) channel.
INPUT_SDK_PATH_PREFIXES = ("messages[]", "prompt", "stop", "tools[]", "response_format")

_MESSAGES_KEY = "messages"
_PROMPT_KEY = "prompt"
_STOP_KEY = "stop"
_ROLE_KEY = "role"
_CONTENT_KEY = "content"
_PART_TEXT_KEY = "text"
_TOOL_CALLS_KEY = "tool_calls"
_FUNCTION_CALL_KEY = "function_call"
_FUNCTION_KEY = "function"
_ARGUMENTS_KEY = "arguments"
#: The role a legacy completions ``prompt`` is sent upstream as (one user turn).
_PROMPT_ROLE = "user"

_CHANNEL_MESSAGE_CONTENT = "message_content"
_CHANNEL_CONTENT_PART_TEXT = "content_part_text"
_CHANNEL_TOOL_ARGUMENTS = "tool_arguments"
_CHANNEL_FUNCTION_ARGUMENTS = "function_arguments"
_CHANNEL_COMPLETION_PROMPT = "completion_prompt"
_CHANNEL_STOP_SEQUENCE = "stop_sequence"
_TOOL_CALL_ID_KEY = "tool_call_id"

_FOLDED_INPUT_CHANNELS = frozenset(
    (
        _CHANNEL_MESSAGE_CONTENT,
        _CHANNEL_CONTENT_PART_TEXT,
        _CHANNEL_TOOL_ARGUMENTS,
        _CHANNEL_FUNCTION_ARGUMENTS,
        _CHANNEL_COMPLETION_PROMPT,
        _CHANNEL_STOP_SEQUENCE,
        "tool_arguments_raw",
        "tool_name",
        "tool_call_id",
        "tool_schema_struct",
        "tool_description",
        "tool_schema_text",
        "response_format_schema",
    )
)


class ChannelFoldError(RuntimeError):
    """The fold is not total over the input ``TEXT_CHANNELS`` (C21), raised at import."""


def fold_request(
    parsed: Mapping[str, object], bounds: ToolBounds | None = None
) -> tuple[FoldResult, UpstreamTemplate]:
    """Fold every input text occurrence and build the upstream template in ONE pass.

    ``bounds`` enables the GW34 tool fold (``tools``, ``tool_choice``, ``response_format``, tool
    history). Without it (a route with no ``tool_bounds``) tool-bearing requests are refused
    earlier by ``forwarded_fields``; a hand-built body that still carries ``tool_calls`` folds the
    legacy scan-only ``tool_arguments`` pair so the fold stays total.
    """
    builder = Builder()
    tools = ToolsTemplate()
    messages: list[TemplateMessage] = []
    raw_messages = parsed.get(_MESSAGES_KEY)
    next_index = 0
    if bounds is not None:
        tools, declared = fold_tools(builder, parsed, bounds)
        validate_tool_messages(raw_messages, declared, bounds)
    if isinstance(raw_messages, list):
        for index, message in enumerate(raw_messages):
            if not isinstance(message, dict):
                continue
            content = _fold_content(builder, message, index)
            calls: tuple[TemplateToolCall, ...] = ()
            call_id: str | None = None
            if bounds is not None:
                calls = fold_tool_calls(builder, message, bounds)
                raw_id = message.get(_TOOL_CALL_ID_KEY)
                if isinstance(raw_id, str):
                    builder.scan_only("tool_call_id", raw_id)
                    call_id = raw_id
            else:
                _fold_tool_calls(builder, message, index)
            _fold_function_call(builder, message, index)
            role = message.get(_ROLE_KEY)
            role_text = role if isinstance(role, str) else ""
            messages.append(
                TemplateMessage(
                    role=role_text, content=content, tool_calls=calls, tool_call_id=call_id
                )
            )
        next_index = len(raw_messages)
    prompt = _fold_strings(builder, parsed.get(_PROMPT_KEY), _CHANNEL_COMPLETION_PROMPT, next_index)
    if prompt is not None:
        messages.append(TemplateMessage(role=_PROMPT_ROLE, content=prompt))
        next_index += 1
    stop = _fold_strings(
        builder, parsed.get(_STOP_KEY), _CHANNEL_STOP_SEQUENCE, next_index, separate=True
    )
    next_index += len(stop) if isinstance(stop, tuple) else (1 if stop is not None else 0)
    fold, forwarded, pair_parts, joined = builder.finish(next_index)
    if bounds is not None and len(joined) > bounds.max_scanned_slots:
        raise MalformedRequest(MALFORMED_WRONG_SHAPE, param="tools")
    template = UpstreamTemplate(
        messages=tuple(messages),
        stop=stop,
        slot_count=len(fold.pairs),
        tools=tools,
        forwarded=forwarded,
        pair_parts=pair_parts,
        joined=joined,
    )
    return fold, template


def fold_input_channels(
    parsed: Mapping[str, object], bounds: ToolBounds | None = None
) -> FoldResult:
    """The fold half of :func:`fold_request` (the scanned pairs + their ``messages[]`` index)."""
    return fold_request(parsed, bounds)[0]


def _fold_content(builder: Builder, message: Mapping[str, object], index: int) -> ContentTemplate:
    """``messages[].content``: a string is one slot; a list folds each part's string ``text``."""
    content = message.get(_CONTENT_KEY)
    if isinstance(content, str):
        return builder.slot(_CHANNEL_MESSAGE_CONTENT, content, index)
    if isinstance(content, list):
        slots: list[TextSlot] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get(_PART_TEXT_KEY)
            if isinstance(text, str):
                slots.append(builder.slot(_CHANNEL_CONTENT_PART_TEXT, text, index))
        return tuple(slots)
    return None


def _fold_tool_calls(builder: Builder, message: Mapping[str, object], index: int) -> None:
    """``messages[].tool_calls[].function.arguments`` (scanned; never forwarded)."""
    tool_calls = message.get(_TOOL_CALLS_KEY)
    if not isinstance(tool_calls, list):
        return
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        function = call.get(_FUNCTION_KEY)
        arguments = function.get(_ARGUMENTS_KEY) if isinstance(function, dict) else None
        if isinstance(arguments, str):
            builder.slot(_CHANNEL_TOOL_ARGUMENTS, arguments, index, forwarded=False)


def _fold_function_call(builder: Builder, message: Mapping[str, object], index: int) -> None:
    """The legacy ``messages[].function_call.arguments`` (scanned; never forwarded)."""
    function_call = message.get(_FUNCTION_CALL_KEY)
    if not isinstance(function_call, dict):
        return
    arguments = function_call.get(_ARGUMENTS_KEY)
    if isinstance(arguments, str):
        builder.slot(_CHANNEL_FUNCTION_ARGUMENTS, arguments, index, forwarded=False)


def _fold_strings(
    builder: Builder,
    value: object,
    channel: str,
    first_index: int,
    *,
    separate: bool = False,
) -> TextSlot | tuple[TextSlot, ...] | None:
    """A string field (``prompt`` / ``stop``) or a list of strings, one slot per string.

    ``separate`` gives each string its own index (``stop`` sequences are independent strings);
    otherwise every string shares ``first_index``. Non-string list items fold nothing.
    """
    if isinstance(value, str):
        return builder.slot(channel, value, first_index)
    if not isinstance(value, list):
        return None
    slots: list[TextSlot] = []
    for offset, item in enumerate(value):
        if isinstance(item, str):
            index = first_index + offset if separate else first_index
            slots.append(builder.slot(channel, item, index))
    return tuple(slots)


def _input_channel_names() -> frozenset[str]:
    """The ``TEXT_CHANNELS`` names whose ``sdk_path`` is in the chat-input scope."""
    return frozenset(
        channel.name
        for channel in TEXT_CHANNELS
        if channel.sdk_path.startswith(INPUT_SDK_PATH_PREFIXES)
    )


def _assert_fold_total() -> None:
    """Fail the build if the fold is not total over the input channels (C21)."""
    expected = _input_channel_names()
    if _FOLDED_INPUT_CHANNELS != expected:
        missing = tuple(sorted(expected - _FOLDED_INPUT_CHANNELS))
        extra = tuple(sorted(_FOLDED_INPUT_CHANNELS - expected))
        raise ChannelFoldError(
            "input-channel fold is not total over TEXT_CHANNELS "
            f"(unfolded input channels: {missing}; folding paths with no "
            f"TEXT_CHANNELS entry: {extra})"
        )


_assert_fold_total()
