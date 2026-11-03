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
``messages[].tool_calls[].function.arguments``, ``messages[].function_call.arguments``, the legacy
completions ``prompt`` (string or list of strings) and ``stop`` (string or list of strings).
PURE: no I/O, no security decision; the forwarding policy lives in
:mod:`gateway_v2.edge.wire.openai_params`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from gateway_v2.domain.channels import TEXT_CHANNELS

__all__ = (
    "INPUT_SDK_PATH_PREFIXES",
    "ChannelFoldError",
    "FoldResult",
    "TemplateMessage",
    "TextSlot",
    "UpstreamTemplate",
    "fold_input_channels",
    "fold_request",
)

#: The ``TextChannel.sdk_path`` prefixes that mark a chat-input (request-side) channel.
INPUT_SDK_PATH_PREFIXES = ("messages[]", "prompt", "stop")

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

_FOLDED_INPUT_CHANNELS = frozenset(
    (
        _CHANNEL_MESSAGE_CONTENT,
        _CHANNEL_CONTENT_PART_TEXT,
        _CHANNEL_TOOL_ARGUMENTS,
        _CHANNEL_FUNCTION_ARGUMENTS,
        _CHANNEL_COMPLETION_PROMPT,
        _CHANNEL_STOP_SEQUENCE,
    )
)


class ChannelFoldError(RuntimeError):
    """The fold is not total over the input ``TEXT_CHANNELS`` (C21), raised at import."""


@dataclass(frozen=True, slots=True)
class FoldResult:
    """Folded ``(channel_name, text)`` pairs in document order + a PARALLEL ``messages[]`` index.

    A ``prompt`` / ``stop`` occurrence gets an index past the last message (each ``stop`` string
    its own), so the two tuples always have the same length and no join run spans them.
    """

    pairs: tuple[tuple[str, str], ...]
    message_index: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TextSlot:
    """Placeholder for fold pair ``index`` (whose channel is ``channel``) in the template.

    Not a JSON value, so nothing parsed from a request body can be mistaken for one.
    """

    index: int
    channel: str


#: A message ``content`` in the template: one slot (string content), a tuple of slots (text parts,
#: rendered joined with no separator -- the scanned byte sequence), or ``None`` (absent / null).
ContentTemplate = TextSlot | tuple[TextSlot, ...] | None


@dataclass(frozen=True, slots=True)
class TemplateMessage:
    """One forwarded message: its role and its content template. Nothing else is copied."""

    role: str
    content: ContentTemplate


@dataclass(frozen=True, slots=True)
class UpstreamTemplate:
    """The forwardable request structure with every scanned text occurrence as a :class:`TextSlot`.

    ``slot_count`` equals the number of fold pairs, so a renderer can check it was handed exactly
    the deltas this template was built against.
    """

    messages: tuple[TemplateMessage, ...]
    stop: TextSlot | tuple[TextSlot, ...] | None
    slot_count: int


class _Builder:
    """Accumulates fold pairs and hands out the matching slots (one loop, one order)."""

    __slots__ = ("index", "pairs")

    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []
        self.index: list[int] = []

    def slot(self, channel: str, text: str, message_index: int) -> TextSlot:
        slot = TextSlot(index=len(self.pairs), channel=channel)
        self.pairs.append((channel, text))
        self.index.append(message_index)
        return slot

    def result(self) -> FoldResult:
        return FoldResult(pairs=tuple(self.pairs), message_index=tuple(self.index))


def fold_request(parsed: Mapping[str, object]) -> tuple[FoldResult, UpstreamTemplate]:
    """Fold every input text occurrence and build the upstream template in ONE pass."""
    builder = _Builder()
    messages: list[TemplateMessage] = []
    raw_messages = parsed.get(_MESSAGES_KEY)
    next_index = 0
    if isinstance(raw_messages, list):
        for index, message in enumerate(raw_messages):
            if not isinstance(message, dict):
                continue
            content = _fold_content(builder, message, index)
            _fold_tool_calls(builder, message, index)
            _fold_function_call(builder, message, index)
            role = message.get(_ROLE_KEY)
            role_text = role if isinstance(role, str) else ""
            messages.append(TemplateMessage(role=role_text, content=content))
        next_index = len(raw_messages)
    prompt = _fold_strings(builder, parsed.get(_PROMPT_KEY), _CHANNEL_COMPLETION_PROMPT, next_index)
    if prompt is not None:
        messages.append(TemplateMessage(role=_PROMPT_ROLE, content=prompt))
        next_index += 1
    stop = _fold_strings(
        builder, parsed.get(_STOP_KEY), _CHANNEL_STOP_SEQUENCE, next_index, separate=True
    )
    fold = builder.result()
    template = UpstreamTemplate(
        messages=tuple(messages), stop=stop, slot_count=len(fold.pairs)
    )
    return fold, template


def fold_input_channels(parsed: Mapping[str, object]) -> FoldResult:
    """The fold half of :func:`fold_request` (the scanned pairs + their ``messages[]`` index)."""
    return fold_request(parsed)[0]


def _fold_content(builder: _Builder, message: Mapping[str, object], index: int) -> ContentTemplate:
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


def _fold_tool_calls(builder: _Builder, message: Mapping[str, object], index: int) -> None:
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
            builder.slot(_CHANNEL_TOOL_ARGUMENTS, arguments, index)


def _fold_function_call(builder: _Builder, message: Mapping[str, object], index: int) -> None:
    """The legacy ``messages[].function_call.arguments`` (scanned; never forwarded)."""
    function_call = message.get(_FUNCTION_CALL_KEY)
    if not isinstance(function_call, dict):
        return
    arguments = function_call.get(_ARGUMENTS_KEY)
    if isinstance(arguments, str):
        builder.slot(_CHANNEL_FUNCTION_ARGUMENTS, arguments, index)


def _fold_strings(
    builder: _Builder,
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
