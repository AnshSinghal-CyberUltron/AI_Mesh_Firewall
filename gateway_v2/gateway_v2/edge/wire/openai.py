"""OpenAI JSON codec + channel folding (GW15, task 1).

``edge/wire/openai.py`` is the route-specific wire logic for the OpenAI-compatible
chat/completions surface: it turns an SDK request body into the internal
:class:`CanonicalRequest` value and (in task 1.3) renders an enforced upstream
response back into one OpenAI-compatible JSON body. ``edge/wire`` is the only
layer permitted to render the OpenAI wire shapes (the import-linter layer order
``edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime >
contracts > domain`` puts ``edge`` at the top; this module imports only
``domain`` for the :class:`~gateway_v2.domain.plan.Surface` enum and the
``TEXT_CHANNELS`` enumeration the fold walks).

**Wire logic only -- no security decision (R1.5).** This module is PURE over the
request bytes: :meth:`OpenAIRequestParser.parse` performs no I/O, resolves no
plan, authenticates nothing, and makes no block/redact decision. It parses the
body into channels + routing fields + the stream flag and nothing more; every
security decision lives in the ``edge/openai_chat.py`` lifecycle (task 2), one
layer up. A body that is malformed or off the OpenAI-compatible contract raises
:class:`MalformedRequest` carrying a *declared value-code* (not a raw framework
``json.JSONDecodeError`` / ``KeyError``); the handler renders that code through
the single :mod:`gateway_v2.edge.errors` ``Error_Envelope`` (R1.4), so the SDK
never sees a raw internal error.

**Streaming disposition (R1.3).** The body ``"stream": true`` flag is recorded
on the :class:`CanonicalRequest` as its ``streaming`` disposition; the lifecycle
later dispatches to the streaming or non-streaming path on that flag alone.

**No capacity literal, no module-level mutable.** The declared malformed
value-codes are plain module-level strings (a value-code is a code, not a
capacity-position literal -- the serving bounds live in ``runtime/resources.py``);
there is no module-level list / dict / set.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from gateway_v2.domain.channels import TEXT_CHANNELS
from gateway_v2.domain.plan import Surface

__all__ = (
    "MALFORMED_BODY",
    "MALFORMED_MISSING_MODEL",
    "MALFORMED_NOT_AN_OBJECT",
    "MALFORMED_WRONG_SHAPE",
    "CanonicalRequest",
    "ChannelFoldError",
    "MalformedRequest",
    "OpenAIRequestParser",
    "OpenAIResponseRenderer",
    "fold_input_channels",
)


# --------------------------------------------------------------------------- #
# Declared malformed value-codes (R1.4) -- plain strings, not framework errors.
# --------------------------------------------------------------------------- #
#
# A malformed / off-contract body raises :class:`MalformedRequest` carrying ONE
# of these declared codes. They are codes (the shared value-code discipline, like
# ``domain/posture.py``), NOT HTTP objects and NOT capacity-position literals: the
# handler maps the code to an OpenAI-shaped error through the single
# ``edge/errors`` ``Error_Envelope`` (an unmapped code already renders a declared
# generic error there, so no raw ``json``/``Key`` error ever reaches the client).

#: The request body is not valid JSON (the bytes could not be decoded/parsed).
MALFORMED_BODY = "malformed_request_body"

#: The decoded JSON top level is not an object (OpenAI requests are JSON objects).
MALFORMED_NOT_AN_OBJECT = "malformed_request_not_an_object"

#: A required OpenAI-contract field is absent -- e.g. ``model`` (R1.1 routing field).
MALFORMED_MISSING_MODEL = "malformed_request_missing_model"

#: A present field holds the wrong type for the OpenAI-compatible shape (e.g.
#: ``model`` not a string, ``stream`` not a bool, ``messages`` not a list).
MALFORMED_WRONG_SHAPE = "malformed_request_wrong_shape"


# --------------------------------------------------------------------------- #
# Exceptions.
# --------------------------------------------------------------------------- #


class MalformedRequest(Exception):
    """A request body is malformed or off the OpenAI-compatible contract (R1.4).

    Carries a *declared value-code* (one of the ``MALFORMED_*`` constants above)
    rather than a raw framework error, so the lifecycle handler renders it through
    the single ``edge/errors`` ``Error_Envelope`` and the SDK sees an
    OpenAI-shaped error -- never a leaked ``json.JSONDecodeError`` / ``KeyError``.
    The originating ``code`` is kept on the instance so the trace keeps the one
    spelling.
    """

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------- #
# Value types (Data Models).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CanonicalRequest:
    """The internal value parsed from an SDK request (R1.1-R1.3, C21).

    Carries every scannable text channel plus the routing fields and the stream
    flag so plan resolution and detection consume one canonical shape regardless
    of surface:

    * ``model`` -- the upstream model routing field (R1.1).
    * ``streaming`` -- the body ``"stream": true`` disposition (R1.3); the
      lifecycle dispatches to the streaming vs non-streaming path on this alone.
    * ``input_channels`` -- the folded ``(channel_name, text)`` pairs, one per
      concrete input ``TextChannel`` occurrence, so a canary in ANY input field
      is enumerated into scannable text (R1.2, C21). The fold itself lands in
      task 1.2 (:func:`fold_input_channels`); the parser packs its result here.
    * ``text_deltas`` -- the ``(channel_name, text)`` payload shape the shipped
      ``dispatch.transform.GatewayRequest`` consumes, so the parsed content
      survives the request -> provider -> event -> downstream path without
      reshaping.
    * ``surface`` -- chat vs completions (the ``Surface`` enum), carried so plan
      resolution and ``applicable_rules`` filter to the request's surface.

    Frozen + slotted: an immutable value built once per request. ``input_channels``
    and ``text_deltas`` are tuples so the value type carries no mutable state.
    """

    model: str
    streaming: bool
    input_channels: tuple[tuple[str, str], ...]
    text_deltas: tuple[tuple[str, str], ...]
    surface: Surface


# --------------------------------------------------------------------------- #
# Parser (R1.1-R1.5) -- pure over the request bytes, no security decision.
# --------------------------------------------------------------------------- #

#: The OpenAI-contract body keys this parser reads. Named constants (not inline
#: literals) so the parse side keeps one spelling of each field.
_BODY_MODEL_KEY = "model"
_BODY_STREAM_KEY = "stream"
_BODY_MESSAGES_KEY = "messages"


class OpenAIRequestParser:
    """SDK JSON body -> :class:`CanonicalRequest`. Pure; no I/O, no security decision.

    Stateless (``__slots__ = ()``), so a single shared instance is safe to reuse
    across every request. :meth:`parse` validates ONLY the OpenAI-compatible wire
    shape and extracts the routing fields + the stream flag; it makes no block /
    redact / auth / plan decision (R1.5). Any shape failure raises
    :class:`MalformedRequest` with a declared value-code (R1.4).
    """

    __slots__ = ()

    def parse(self, body: bytes, *, surface: Surface) -> CanonicalRequest:
        """Parse a request body into a :class:`CanonicalRequest` (R1.1-R1.3).

        Decodes ``body`` as JSON, validates the OpenAI-compatible shape, reads the
        ``model`` routing field and the ``stream`` disposition, and packs the
        folded input channels. The parse is PURE: it touches no I/O, resolves no
        plan, and makes no security decision (R1.5). A body that is not JSON, not
        a JSON object, missing a required field, or carrying a wrong-typed field
        raises :class:`MalformedRequest` with the matching declared value-code
        (R1.4) -- never a raw framework error.
        """
        obj = self._decode(body)
        model = self._require_model(obj)
        streaming = self._stream_flag(obj)
        self._check_messages_shape(obj)
        channels = fold_input_channels(obj)
        return CanonicalRequest(
            model=model,
            streaming=streaming,
            input_channels=channels,
            text_deltas=channels,
            surface=surface,
        )

    @staticmethod
    def _decode(body: bytes) -> Mapping[str, object]:
        """Decode the body as a JSON object, or raise a declared value-code (R1.4).

        A body that is not valid JSON raises :data:`MALFORMED_BODY`; a body whose
        top level is not a JSON object raises :data:`MALFORMED_NOT_AN_OBJECT`. The
        raw ``json`` decode error is caught and never propagated to the caller.
        """
        try:
            decoded = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise MalformedRequest(MALFORMED_BODY) from exc
        if not isinstance(decoded, dict):
            raise MalformedRequest(MALFORMED_NOT_AN_OBJECT)
        return decoded

    @staticmethod
    def _require_model(obj: Mapping[str, object]) -> str:
        """Read the required ``model`` routing field as a string (R1.1).

        A missing ``model`` raises :data:`MALFORMED_MISSING_MODEL`; a present but
        non-string ``model`` raises :data:`MALFORMED_WRONG_SHAPE`.
        """
        if _BODY_MODEL_KEY not in obj:
            raise MalformedRequest(MALFORMED_MISSING_MODEL)
        model = obj[_BODY_MODEL_KEY]
        if not isinstance(model, str):
            raise MalformedRequest(MALFORMED_WRONG_SHAPE)
        return model

    @staticmethod
    def _stream_flag(obj: Mapping[str, object]) -> bool:
        """Read the ``stream`` disposition (R1.3); default false when absent.

        An absent ``stream`` key means non-streaming (the OpenAI default). A
        present but non-bool ``stream`` raises :data:`MALFORMED_WRONG_SHAPE`
        rather than coercing a truthy value, so the streaming disposition is never
        guessed from an off-contract type.
        """
        if _BODY_STREAM_KEY not in obj:
            return False
        stream = obj[_BODY_STREAM_KEY]
        if not isinstance(stream, bool):
            raise MalformedRequest(MALFORMED_WRONG_SHAPE)
        return stream

    @staticmethod
    def _check_messages_shape(obj: Mapping[str, object]) -> None:
        """Validate the ``messages`` field shape when present (R1.4).

        ``messages`` is optional at the parser level (``/v1/completions`` carries
        ``prompt`` instead), but when present it MUST be a list of objects -- the
        OpenAI-compatible shape the task 1.2 fold walks. A present ``messages``
        that is not a list, or that holds a non-object element, raises
        :data:`MALFORMED_WRONG_SHAPE`. No security decision is made here; this is
        a pure shape check so the fold in task 1.2 can rely on the structure.
        """
        if _BODY_MESSAGES_KEY not in obj:
            return
        messages = obj[_BODY_MESSAGES_KEY]
        if not isinstance(messages, list):
            raise MalformedRequest(MALFORMED_WRONG_SHAPE)
        for message in messages:
            if not isinstance(message, dict):
                raise MalformedRequest(MALFORMED_WRONG_SHAPE)

# --------------------------------------------------------------------------- #
# Input-channel folding (R1.2, C21) -- total over the input TEXT_CHANNELS.
# --------------------------------------------------------------------------- #
#
# ``fold_input_channels`` walks a parsed OpenAI request against the INPUT-scoped
# entries of ``domain/channels.py::TEXT_CHANNELS`` and yields one
# ``(channel_name, text)`` pair per concrete text occurrence, so a canary in ANY
# input field is enumerated into scannable text (R1.2). The channel name emitted
# for each pair is the ``TextChannel.name`` (``"message_content"`` /
# ``"content_part_text"`` / ``"tool_arguments"`` / ``"function_arguments"``), so
# the input scan and the Property-2 canary test key on the SAME spelling the
# channel enumeration pins.
#
# INPUT vs OUTPUT scope. ``TEXT_CHANNELS`` enumerates both the request-side
# channels (``messages[]...``) and the response-side channels (``choices[]...``).
# The six ``choices[]`` entries are OUTPUT-scoped and belong to the output scan
# (task 4); the input fold handles ONLY the four ``messages[]`` entries. Scope is
# read off the authoritative ``sdk_path`` prefix -- ``_INPUT_SDK_PATH_PREFIX`` --
# NOT a second hand-maintained list, so adding an input channel to the one
# ``TEXT_CHANNELS`` tuple is all it takes for the totality check below to demand a
# folding path for it.
#
# TOTALITY (C21). The fold is total over the input channels: the set of channel
# names the fold can emit (``_FOLDED_INPUT_CHANNELS``) must equal the set of
# input-scoped ``TEXT_CHANNELS`` names (``_INPUT_CHANNEL_NAMES``). If they ever
# diverge -- a new input ``TextChannel`` added with no folding path, or a folding
# path naming a channel the enumeration dropped -- the module-level consistency
# check raises :class:`ChannelFoldError` at import time, so a new SDK field cannot
# silently escape scanning. This is the local, concrete form of the design's
# "``unscanned_channels()`` must stay empty": here it means no INPUT channel is
# left unfolded. The check runs once at import (not per request) and uses only
# immutable constructs (``frozenset`` / ``tuple``), so there is no module-level
# mutable.

#: The ``TextChannel.sdk_path`` prefix that marks an INPUT (request-side) channel.
#: Output channels carry the ``choices[]`` prefix and are the output scan's job.
_INPUT_SDK_PATH_PREFIX = "messages[]"

#: OpenAI message/content/tool keys the fold reads. Named constants so the fold
#: keeps one spelling of each field (mirrors the parser's ``_BODY_*`` keys).
_MESSAGE_CONTENT_KEY = "content"
_CONTENT_PART_TEXT_KEY = "text"
_MESSAGE_TOOL_CALLS_KEY = "tool_calls"
_MESSAGE_FUNCTION_CALL_KEY = "function_call"
_FUNCTION_KEY = "function"
_FUNCTION_ARGUMENTS_KEY = "arguments"

#: The channel names the fold emits, each tied to the ``TextChannel.name`` the
#: enumeration pins. A frozenset (not a list/dict/set literal) so there is no
#: module-level mutable; the totality check below asserts this equals the set of
#: input-scoped ``TEXT_CHANNELS`` names.
_CHANNEL_MESSAGE_CONTENT = "message_content"
_CHANNEL_CONTENT_PART_TEXT = "content_part_text"
_CHANNEL_TOOL_ARGUMENTS = "tool_arguments"
_CHANNEL_FUNCTION_ARGUMENTS = "function_arguments"

_FOLDED_INPUT_CHANNELS = frozenset(
    (
        _CHANNEL_MESSAGE_CONTENT,
        _CHANNEL_CONTENT_PART_TEXT,
        _CHANNEL_TOOL_ARGUMENTS,
        _CHANNEL_FUNCTION_ARGUMENTS,
    )
)


class ChannelFoldError(RuntimeError):
    """The fold is not total over the input ``TEXT_CHANNELS`` (C21).

    Raised at import time by the module-level consistency check when the set of
    channel names :func:`fold_input_channels` can emit diverges from the set of
    input-scoped ``TEXT_CHANNELS`` names -- i.e. a new input ``TextChannel`` was
    added with no folding path, or a folding path names a channel the enumeration
    no longer carries. A build-time failure (not a silent gap) so a new SDK field
    cannot escape scanning.
    """


def _input_channel_names() -> frozenset[str]:
    """The set of input-scoped ``TEXT_CHANNELS`` names (C21 scope boundary).

    An INPUT channel is one whose ``sdk_path`` begins with
    :data:`_INPUT_SDK_PATH_PREFIX` (``messages[]``); the ``choices[]`` entries are
    OUTPUT-scoped and excluded. Derived from the authoritative ``TEXT_CHANNELS``
    tuple so there is one source of truth for the channel set.
    """
    return frozenset(
        channel.name
        for channel in TEXT_CHANNELS
        if channel.sdk_path.startswith(_INPUT_SDK_PATH_PREFIX)
    )


def fold_input_channels(parsed: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    """Enumerate every input ``TextChannel`` occurrence into pairs (R1.2, C21).

    Walks ``parsed`` (an already-decoded OpenAI request object) against the four
    input-scoped ``TEXT_CHANNELS`` entries and returns a tuple of
    ``(channel_name, text)`` pairs -- one per concrete text occurrence, in
    document order:

    * ``messages[].content`` -> ``message_content`` when the content is a plain
      string (the common OpenAI shape).
    * ``messages[].content[].text`` -> ``content_part_text`` for each ``text``
      part when the content is the array-of-parts shape.
    * ``messages[].tool_calls[].function.arguments`` -> ``tool_arguments`` for
      each tool call's ``arguments`` string.
    * ``messages[].function_call.arguments`` -> ``function_arguments`` for the
      legacy single ``function_call`` ``arguments`` string.

    Only well-typed ``str`` occurrences are folded; a mistyped field is simply not
    a text occurrence (the parser already rejected an off-contract ``messages``
    *shape* -- a list of objects -- so this walk is defensive about per-field
    types without re-raising). The fold is PURE: no I/O, no security decision; the
    input scan runs the injected detector over the returned pairs. The fold is
    TOTAL over the input channels -- the module-level check below guarantees every
    input ``TextChannel`` has a path here (C21).
    """
    messages = parsed.get(_BODY_MESSAGES_KEY)
    if not isinstance(messages, list):
        return ()
    pairs: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        _fold_message_content(message, pairs)
        _fold_tool_calls(message, pairs)
        _fold_function_call(message, pairs)
    return tuple(pairs)


def _fold_message_content(
    message: Mapping[str, object], pairs: list[tuple[str, str]]
) -> None:
    """Fold ``messages[].content`` -- string form OR array-of-parts (R1.2).

    A plain-string ``content`` folds as ``message_content``; the OpenAI
    array-of-parts shape folds each part's ``text`` as ``content_part_text`` in
    order. A content that is neither a string nor a list contributes no text.
    """
    content = message.get(_MESSAGE_CONTENT_KEY)
    if isinstance(content, str):
        pairs.append((_CHANNEL_MESSAGE_CONTENT, content))
        return
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get(_CONTENT_PART_TEXT_KEY)
            if isinstance(text, str):
                pairs.append((_CHANNEL_CONTENT_PART_TEXT, text))


def _fold_tool_calls(
    message: Mapping[str, object], pairs: list[tuple[str, str]]
) -> None:
    """Fold ``messages[].tool_calls[].function.arguments`` (R1.2).

    Each tool call's ``function.arguments`` string folds as ``tool_arguments`` in
    order; a tool call with no well-typed ``arguments`` string contributes none.
    """
    tool_calls = message.get(_MESSAGE_TOOL_CALLS_KEY)
    if not isinstance(tool_calls, list):
        return
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        arguments = _function_arguments(call)
        if arguments is not None:
            pairs.append((_CHANNEL_TOOL_ARGUMENTS, arguments))


def _fold_function_call(
    message: Mapping[str, object], pairs: list[tuple[str, str]]
) -> None:
    """Fold the legacy ``messages[].function_call.arguments`` (R1.2).

    The single legacy ``function_call`` object's ``arguments`` string folds as
    ``function_arguments``; a missing or mistyped ``arguments`` contributes none.
    """
    function_call = message.get(_MESSAGE_FUNCTION_CALL_KEY)
    if not isinstance(function_call, dict):
        return
    arguments = function_call.get(_FUNCTION_ARGUMENTS_KEY)
    if isinstance(arguments, str):
        pairs.append((_CHANNEL_FUNCTION_ARGUMENTS, arguments))


def _function_arguments(call: Mapping[str, object]) -> str | None:
    """Read ``<call>.function.arguments`` as a string, or ``None`` if absent/mistyped."""
    function = call.get(_FUNCTION_KEY)
    if not isinstance(function, dict):
        return None
    arguments = function.get(_FUNCTION_ARGUMENTS_KEY)
    return arguments if isinstance(arguments, str) else None


def _assert_fold_total() -> None:
    """Fail the build if the fold is not total over the input channels (C21).

    Compares the channel names :func:`fold_input_channels` can emit
    (:data:`_FOLDED_INPUT_CHANNELS`) against the input-scoped ``TEXT_CHANNELS``
    names (:func:`_input_channel_names`). Any divergence raises
    :class:`ChannelFoldError` at import, so a new input ``TextChannel`` with no
    folding path -- or a dangling folding path -- is a build-time failure rather
    than a field that silently escapes scanning.
    """
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


# --------------------------------------------------------------------------- #
# Non-streaming response render (R9.1) -- enforced deltas -> ONE OpenAI JSON.
# --------------------------------------------------------------------------- #
#
# ``OpenAIResponseRenderer`` is the OUTPUT half of this module's wire logic: it
# places the already-enforced upstream text (the ``(channel_name, delta_text)``
# pairs the output guard has run ``apply_decision`` over -- see task 4's
# ``collect_and_render``) into ONE OpenAI-compatible non-streaming JSON body (the
# ``/v1/chat/completions`` non-stream shape the stock SDK parses as a
# ``ChatCompletion``). It is PURE wire logic and makes NO security decision
# (R1.5): the caller has ALREADY enforced the bytes, so the renderer only
# transports them into the JSON envelope -- it never re-scans, re-decides, or
# mutates the enforced text.
#
# BYTE-STABILITY (Property 1 round-trip / Property 14 non-stream enforced JSON).
# The content assembled into the response is EXACTLY the concatenation of the
# enforced ``deltas`` text for the output content channel -- no mutation and no
# re-encoding that changes bytes. ``json.dumps`` runs with ``ensure_ascii=True``
# (every non-ASCII code point becomes a ``\uXXXX`` escape, matching the recorded
# conformance frames + the SSE codec in ``sse.py``) and ``separators=(",", ":")``
# (no insignificant whitespace), and the JSON object is built with a FIXED key
# order, so the same enforced deltas always render the same bytes and
# ``json.loads`` recovers the content byte-for-byte. The content string itself is
# the raw concatenation of the delta text -- a round-trip through
# ``json.dumps``/``json.loads`` is byte-identity on that string.
#
# CHANNEL MAPPING. The transport ``channel_name`` is the shipped
# ``domain/channels.py`` spelling the output scan keys on (``output_content`` for
# the assistant message text, ``output_reasoning`` /``output_refusal`` for the
# reasoning / refusal fields, ``output_tool_arguments`` for a tool call's
# ``function.arguments``). The default egress ``content`` channel (the SSE
# codec's delta key) is treated as output content too, so a stream collected into
# non-stream form renders identically regardless of which spelling the upstream
# used. One output ``message`` is produced (the non-stream shape has a single
# ``choices[0]``); the streaming path handles per-frame fragment reassembly
# separately.

#: The non-streaming SDK response ``object`` discriminator. The stock OpenAI SDK
#: parses a non-streamed body as a ``ChatCompletion`` only when this reads exactly
#: ``chat.completion`` (matches the recorded conformance body in
#: ``tests/openai_conformance/test_openai_sdk_compat.py``). A plain string, not a
#: capacity literal.
COMPLETION_OBJECT = "chat.completion"

#: Defaults for the SDK-required ``id`` / ``created`` identity fields. The
#: ``model`` is taken from the request (R1.1 routing field); ``id`` / ``created``
#: are supplied here because the enforced ``deltas`` carry only text, not stream
#: identity (parity with the ``sse.py`` ``DEFAULT_*`` constants). Plain
#: strings/ints, not capacity literals.
DEFAULT_COMPLETION_ID = "chatcmpl-gateway"
DEFAULT_COMPLETION_CREATED = 0

#: The assistant role every chat completion message carries.
_RESPONSE_ROLE = "assistant"

#: Finish reasons the renderer emits: ``tool_calls`` when the enforced output
#: carries tool-call arguments, else ``stop`` (the clean-completion default).
_FINISH_STOP = "stop"
_FINISH_TOOL_CALLS = "tool_calls"

#: The transport channel names whose enforced text is the assistant message
#: ``content`` -- the shipped ``output_content`` output channel plus the default
#: egress ``content`` delta key (``sse.py``), so a collected stream and a direct
#: non-stream completion render the same content bytes. A frozenset (not a
#: list/dict literal) so there is no module-level mutable.
_OUTPUT_CONTENT_CHANNELS = frozenset(("output_content", "content"))

#: The transport channel name carrying tool-call ``function.arguments`` text.
_OUTPUT_TOOL_ARGUMENTS_CHANNEL = "output_tool_arguments"

#: The transport channel name carrying the assistant ``reasoning_content`` text.
_OUTPUT_REASONING_CHANNEL = "output_reasoning"

#: The transport channel name carrying the assistant ``refusal`` text.
_OUTPUT_REFUSAL_CHANNEL = "output_refusal"


class OpenAIResponseRenderer:
    """Enforced upstream deltas -> ONE OpenAI-compatible non-streaming JSON (R9.1).

    Stateless (``__slots__ = ()``), so a single shared instance is safe to reuse
    across every request. The renderer is PURE wire logic: it makes no security
    decision (R1.5) -- the caller's ``apply_decision`` has already enforced
    (possibly redacted) the ``deltas``, so :meth:`non_stream_body` only places
    those exact bytes into the OpenAI JSON envelope. It never re-scans, re-decides,
    or mutates the enforced text, and the content it emits is the byte-for-byte
    concatenation of the output-content deltas (Property 1 / Property 14).
    """

    __slots__ = ()

    def non_stream_body(
        self, req: CanonicalRequest, deltas: tuple[tuple[str, str], ...]
    ) -> bytes:
        """Render ``req`` + enforced ``deltas`` into ONE OpenAI JSON body (R9.1).

        Walks the enforced ``(channel_name, delta_text)`` pairs in order,
        concatenating each channel's text byte-for-byte into the matching
        response field: the output-content channels into the message ``content``,
        ``output_reasoning`` into ``reasoning_content``, ``output_refusal`` into
        ``refusal``, and ``output_tool_arguments`` into a tool call's
        ``function.arguments``. The result is one ``chat.completion`` JSON object
        (``id`` / ``object`` / ``created`` / ``model`` / ``choices[0].message`` /
        ``finish_reason`` / ``usage``) serialised with a fixed key order,
        ``ensure_ascii=True``, and compact separators, so the same enforced deltas
        always render the same bytes and a ``json.loads`` round-trip recovers the
        content byte-for-byte. PURE: no I/O, no security decision (R1.5).
        """
        message = self._message_for(deltas)
        has_tool_calls = "tool_calls" in message
        obj: dict[str, object] = {
            "id": DEFAULT_COMPLETION_ID,
            "object": COMPLETION_OBJECT,
            "created": DEFAULT_COMPLETION_CREATED,
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": (
                        _FINISH_TOOL_CALLS if has_tool_calls else _FINISH_STOP
                    ),
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
        body = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
        return body.encode("ascii")

    @staticmethod
    def _message_for(deltas: tuple[tuple[str, str], ...]) -> dict[str, object]:
        """Assemble the single ``choices[0].message`` object from enforced deltas.

        Concatenates each channel's enforced text in document order so the
        placement is a byte identity on the content. ``content`` is always
        present (``""`` when no output-content delta was released, matching the
        SDK's non-null content shape for a plain completion); ``reasoning_content``
        / ``refusal`` / ``tool_calls`` appear only when their channel carried
        text, so a plain completion renders the minimal SDK shape.
        """
        content = ""
        reasoning = ""
        refusal = ""
        tool_arguments = ""
        for channel, text in deltas:
            if channel in _OUTPUT_CONTENT_CHANNELS:
                content += text
            elif channel == _OUTPUT_REASONING_CHANNEL:
                reasoning += text
            elif channel == _OUTPUT_REFUSAL_CHANNEL:
                refusal += text
            elif channel == _OUTPUT_TOOL_ARGUMENTS_CHANNEL:
                tool_arguments += text
        message: dict[str, object] = {"role": _RESPONSE_ROLE, "content": content}
        if reasoning:
            message["reasoning_content"] = reasoning
        if refusal:
            message["refusal"] = refusal
        if tool_arguments:
            message["tool_calls"] = [
                {
                    "index": 0,
                    "type": "function",
                    "function": {"arguments": tool_arguments},
                }
            ]
        return message
