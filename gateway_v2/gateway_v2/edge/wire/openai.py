"""OpenAI JSON codec + channel folding (GW15, task 1).

``edge/wire/openai.py`` is the route-specific wire logic for the OpenAI-compatible
chat/completions surface: it turns an SDK request body into the internal
:class:`CanonicalRequest` value and renders an enforced upstream response back into
one OpenAI-compatible JSON body. ``edge/wire`` is the only layer permitted to
render the OpenAI wire shapes (``edge`` is the top of the import-linter layer
order). It imports ``domain`` (the :class:`~gateway_v2.domain.plan.Surface` enum +
the ``TEXT_CHANNELS`` enumeration the fold walks) and ``runtime`` (the
:class:`~gateway_v2.runtime.resources.ResourceContract` the parser + clamp read
their token bounds from), both below ``edge`` in the layer order.

**Wire logic only -- no security decision (R1.5).** This module is PURE over the
request bytes: :meth:`OpenAIRequestParser.parse`, :func:`clamp_output_budget` and
:func:`prompt_chars` perform no I/O, resolve no plan, authenticate nothing, and
make no block/redact decision -- every security decision lives in the
``edge/openai_chat.py`` lifecycle, one layer up. A body that is malformed or off
the OpenAI-compatible contract raises :class:`MalformedRequest` carrying a
*declared value-code* (not a raw framework error); the handler renders that code
through the single :mod:`gateway_v2.edge.errors` ``Error_Envelope`` (R1.4).

**Streaming disposition (R1.3).** The body ``"stream": true`` flag is recorded on
the :class:`CanonicalRequest` as its ``streaming`` disposition; the lifecycle
dispatches to the streaming vs non-streaming path on that flag alone.

**No capacity literal, no module-level mutable.** The declared value-codes are
plain module-level strings (codes, not capacity-position literals); the token
bounds come from the injected ``ResourceContract``, so no serving bound is
embedded here, and there is no module-level list / dict / set.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from dataclasses import dataclass

from gateway_v2.domain.channels import TEXT_CHANNELS
from gateway_v2.domain.plan import Surface
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "INVALID_MAX_COMPLETION_TOKENS",
    "INVALID_MAX_TOKENS",
    "MALFORMED_BODY",
    "MALFORMED_MISSING_MODEL",
    "MALFORMED_NOT_AN_OBJECT",
    "MALFORMED_WRONG_SHAPE",
    "CanonicalRequest",
    "ChannelFoldError",
    "FoldResult",
    "MalformedRequest",
    "OpenAIRequestParser",
    "OpenAIResponseRenderer",
    "clamp_output_budget",
    "fold_input_channels",
    "prompt_chars",
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

#: ``max_tokens`` is a well-shaped non-negative int above the parse boundary
#: (``max_output_tokens_boundary()``). v1 parity: refused HTTP 400 with ``param``
#: ``max_tokens`` (``edge/errors`` -> ``(400, "invalid_request_error")``). A code.
INVALID_MAX_TOKENS = "invalid_max_tokens"

#: ``max_completion_tokens`` above the same boundary: the sibling HTTP 400 refusal
#: with ``param`` ``max_completion_tokens``. A code, not a capacity literal.
INVALID_MAX_COMPLETION_TOKENS = "invalid_max_completion_tokens"


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
    spelling. An optional ``param`` names the offending request field (e.g.
    ``"max_tokens"`` for :data:`INVALID_MAX_TOKENS`), matching v1's boundary
    refusal; it defaults to ``None`` so every existing raise site (code only) is
    unchanged, and the render site threads it to the envelope when present.
    """

    __slots__ = ("code", "param")

    def __init__(self, code: str, *, param: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.param = param


# --------------------------------------------------------------------------- #
# Value types (Data Models).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CanonicalRequest:
    """The internal value parsed from an SDK request (R1.1-R1.3, C21).

    Carries every scannable text channel plus the routing fields and the stream
    flag so plan resolution and detection consume one canonical shape regardless of
    surface: ``model`` (the upstream routing field, R1.1), ``streaming`` (the body
    ``"stream": true`` disposition, R1.3), ``input_channels`` (the folded
    ``(channel_name, text)`` pairs, one per concrete input ``TextChannel``
    occurrence, R1.2/C21), ``text_deltas`` (the ``dispatch.transform.GatewayRequest``
    payload shape) and ``surface`` (chat vs completions, the ``Surface`` enum).

    Frozen + slotted: an immutable value built once per request; the tuple fields
    carry no mutable state.

    Group A (``v2-enforcement-leaks``) adds ``input_message_index``: a parallel
    tuple of the 0-based ``messages[]`` index each ``input_channels`` pair came
    from (one int per pair, same order). :func:`prompt_chars` reads it to join the
    content parts of one message with a single separator, matching v1's folded
    input-text length; the input scan reads it to form per-message join runs.

    Group F adds the output-budget fields (v1 parity): ``max_tokens`` (the output
    budget, 0 = unset, validated at parse against ``max_output_tokens_boundary()``),
    ``n`` (completion count, clamped to ``max_completions()`` by
    :func:`clamp_output_budget`), ``max_completion_tokens`` (newer field, validated
    but -- like v1 -- not charged/clamped), and ``request_clamps``
    (:func:`clamp_output_budget`'s ``"<field>=<pre>-><post>"`` records). Every new
    field carries a default, so each existing construction keeps compiling.
    """

    model: str
    streaming: bool
    input_channels: tuple[tuple[str, str], ...]
    text_deltas: tuple[tuple[str, str], ...]
    surface: Surface
    input_message_index: tuple[int, ...] = ()
    max_tokens: int = 0
    n: int = 1
    max_completion_tokens: int | None = None
    request_clamps: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Parser (R1.1-R1.5) -- pure over the request bytes, no security decision.
# --------------------------------------------------------------------------- #

#: The OpenAI-contract body keys this parser reads. Named constants (not inline
#: literals) so the parse side keeps one spelling of each field.
_BODY_MODEL_KEY = "model"
_BODY_STREAM_KEY = "stream"
_BODY_MESSAGES_KEY = "messages"
_BODY_MAX_TOKENS_KEY = "max_tokens"
_BODY_N_KEY = "n"
_BODY_MAX_COMPLETION_TOKENS_KEY = "max_completion_tokens"


class OpenAIRequestParser:
    """SDK JSON body -> :class:`CanonicalRequest`. Pure; no I/O, no security decision.

    Stateless (``__slots__ = ()``), so a single shared instance is safe to reuse.
    :meth:`parse` validates ONLY the OpenAI-compatible wire shape and extracts the
    routing fields, stream flag and output-budget fields; it makes no block / redact
    / auth / plan decision (R1.5). Any shape failure raises
    :class:`MalformedRequest` with a declared value-code (R1.4).
    """

    __slots__ = ()

    def parse(
        self, body: bytes, *, surface: Surface, contract: ResourceContract
    ) -> CanonicalRequest:
        """Parse a request body into a :class:`CanonicalRequest` (R1.1-R1.3, R2.16).

        Decodes ``body`` as JSON, validates the OpenAI shape, reads ``model`` + the
        ``stream`` disposition, packs the folded input channels (with per-pair
        ``messages[]`` index), and reads the output-budget fields (``max_tokens`` /
        ``n`` / ``max_completion_tokens``) through the one :meth:`_int_field` helper.
        PURE (R1.5). The injected ``contract`` supplies the
        ``max_output_tokens_boundary()`` the token fields validate against, so no
        capacity literal is embedded; any shape / range failure raises
        :class:`MalformedRequest` with a declared value-code (R1.4).
        """
        obj = self._decode(body)
        model = self._require_model(obj)
        streaming = self._stream_flag(obj)
        self._check_messages_shape(obj)
        fold = fold_input_channels(obj)
        boundary = contract.max_output_tokens_boundary()
        max_tokens = self._int_field(
            obj,
            _BODY_MAX_TOKENS_KEY,
            default=0,
            minimum=0,
            maximum=boundary,
            over_code=INVALID_MAX_TOKENS,
        )
        n = self._int_field(obj, _BODY_N_KEY, default=1, minimum=1)
        # ``max_completion_tokens`` is ``None`` when absent / explicit null; when
        # present it is validated by the same helper (``default`` then unused).
        max_completion_tokens: int | None = None
        mct = obj.get(_BODY_MAX_COMPLETION_TOKENS_KEY)
        if _BODY_MAX_COMPLETION_TOKENS_KEY in obj and mct is not None:
            max_completion_tokens = self._int_field(
                obj,
                _BODY_MAX_COMPLETION_TOKENS_KEY,
                default=0,
                minimum=0,
                maximum=boundary,
                over_code=INVALID_MAX_COMPLETION_TOKENS,
            )
        return CanonicalRequest(
            model=model,
            streaming=streaming,
            input_channels=fold.pairs,
            text_deltas=fold.pairs,
            surface=surface,
            input_message_index=fold.message_index,
            max_tokens=max_tokens,
            n=n,
            max_completion_tokens=max_completion_tokens,
        )

    @staticmethod
    def _int_field(
        obj: Mapping[str, object],
        key: str,
        *,
        default: int,
        minimum: int,
        maximum: int | None = None,
        over_code: str | None = None,
    ) -> int:
        """Read one off-contract-sensitive integer request field (R1.4, R2.16).

        An absent key or explicit JSON ``null`` yields ``default`` (v1 treats
        ``null`` as absent). A ``bool`` is rejected BEFORE the int check (``bool``
        subclasses ``int``, so ``True`` must not read as ``1``); a ``float`` (incl.
        ``4096.0``), a ``str`` or a value below ``minimum`` (so ``n=0`` and a
        negative ``max_tokens`` fail) raise :data:`MALFORMED_WRONG_SHAPE`. A value
        above ``maximum`` raises :class:`MalformedRequest` with the field-specific
        ``over_code`` and ``param=key`` (so ``boundary`` passes, ``boundary + 1``
        is refused). PURE: a wire-shape + range check only.
        """
        if key not in obj:
            return default
        value = obj[key]
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            raise MalformedRequest(MALFORMED_WRONG_SHAPE)
        if value < minimum:
            raise MalformedRequest(MALFORMED_WRONG_SHAPE)
        if maximum is not None and value > maximum:
            raise MalformedRequest(over_code or MALFORMED_WRONG_SHAPE, param=key)
        return value

    @staticmethod
    def _decode(body: bytes) -> Mapping[str, object]:
        """Decode the body as a JSON object, or raise a declared value-code (R1.4).

        Not valid JSON -> :data:`MALFORMED_BODY`; a non-object top level ->
        :data:`MALFORMED_NOT_AN_OBJECT`. The raw ``json`` error is never propagated.
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

        An absent ``stream`` key means non-streaming (the OpenAI default); a present
        non-bool ``stream`` raises :data:`MALFORMED_WRONG_SHAPE` rather than coercing
        a truthy value, so the disposition is never guessed from an off-contract type.
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
        ``prompt`` instead), but when present it MUST be a list of objects (the
        shape the fold walks). A present ``messages`` that is not a list, or holds a
        non-object element, raises :data:`MALFORMED_WRONG_SHAPE`. A pure shape check,
        no security decision, so the fold can rely on the structure.
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
# ``(channel_name, text)`` pair per concrete text occurrence (R1.2). Each pair's
# channel name is the ``TextChannel.name`` (``message_content`` /
# ``content_part_text`` / ``tool_arguments`` / ``function_arguments``), the SAME
# spelling the input scan and the canary test key on.
#
# INPUT vs OUTPUT scope. ``TEXT_CHANNELS`` enumerates request-side (``messages[]``)
# and response-side (``choices[]``) channels; the ``choices[]`` entries are
# OUTPUT-scoped (the output scan's job) and the input fold handles ONLY the four
# ``messages[]`` entries. Scope is read off the authoritative ``sdk_path`` prefix
# (``_INPUT_SDK_PATH_PREFIX``), not a second hand-maintained list.
#
# TOTALITY (C21). The set of channel names the fold can emit
# (``_FOLDED_INPUT_CHANNELS``) must equal the set of input-scoped ``TEXT_CHANNELS``
# names. Any divergence -- a new input ``TextChannel`` with no folding path, or a
# folding path naming a dropped channel -- raises :class:`ChannelFoldError` at
# import, so a new SDK field cannot silently escape scanning. The check runs once
# at import over immutable constructs (``frozenset`` / ``tuple``).

#: The ``TextChannel.sdk_path`` prefix that marks an INPUT (request-side) channel.
#: Output channels carry the ``choices[]`` prefix and are the output scan's job.
_INPUT_SDK_PATH_PREFIX = "messages[]"

#: OpenAI message/content/tool keys the fold reads (one spelling of each field).
_MESSAGE_CONTENT_KEY = "content"
_CONTENT_PART_TEXT_KEY = "text"
_MESSAGE_TOOL_CALLS_KEY = "tool_calls"
_MESSAGE_FUNCTION_CALL_KEY = "function_call"
_FUNCTION_KEY = "function"
_FUNCTION_ARGUMENTS_KEY = "arguments"

#: The channel names the fold emits, each tied to the ``TextChannel.name`` the
#: enumeration pins. A frozenset (no module-level mutable); the totality check
#: asserts this equals the set of input-scoped ``TEXT_CHANNELS`` names.
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


@dataclass(frozen=True, slots=True)
class FoldResult:
    """The result of :func:`fold_input_channels` (R1.2, C21, Group A Property 3).

    ``pairs`` is the tuple of ``(channel_name, text)`` occurrences in document
    order; ``message_index`` is a PARALLEL tuple of the same length (one int per
    pair) giving the 0-based ``messages[]`` index that pair was folded from, so a
    caller can zip them without a guard. Frozen + slotted.
    """

    pairs: tuple[tuple[str, str], ...]
    message_index: tuple[int, ...]


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


def fold_input_channels(parsed: Mapping[str, object]) -> FoldResult:
    """Enumerate every input ``TextChannel`` occurrence into pairs (R1.2, C21).

    Walks ``parsed`` (an already-decoded OpenAI request object) against the four
    input-scoped ``TEXT_CHANNELS`` entries and returns a :class:`FoldResult`: a
    tuple of ``(channel_name, text)`` pairs (one per concrete text occurrence, in
    document order) and a parallel tuple of each pair's 0-based ``messages[]``
    index (Group A Property 3). The paths folded are ``messages[].content`` ->
    ``message_content`` (string content), ``messages[].content[].text`` ->
    ``content_part_text`` (array-of-parts), ``messages[].tool_calls[].function.
    arguments`` -> ``tool_arguments``, and the legacy
    ``messages[].function_call.arguments`` -> ``function_arguments``.

    Only well-typed ``str`` occurrences are folded; a mistyped field is simply not
    a text occurrence (the parser already rejected an off-contract ``messages``
    shape). PURE: no I/O, no security decision. The fold is TOTAL over the input
    channels (the module-level check guarantees every input ``TextChannel`` has a
    path, C21), and every appended pair records its ``messages[]`` index so the two
    returned tuples stay aligned.
    """
    messages = parsed.get(_BODY_MESSAGES_KEY)
    if not isinstance(messages, list):
        return FoldResult(pairs=(), message_index=())
    pairs: list[tuple[str, str]] = []
    message_index: list[int] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        _fold_message_content(message, index, pairs, message_index)
        _fold_tool_calls(message, index, pairs, message_index)
        _fold_function_call(message, index, pairs, message_index)
    return FoldResult(pairs=tuple(pairs), message_index=tuple(message_index))


def _fold_message_content(
    message: Mapping[str, object],
    index: int,
    pairs: list[tuple[str, str]],
    message_index: list[int],
) -> None:
    """Fold ``messages[].content`` -- string form OR array-of-parts (R1.2).

    A plain-string ``content`` folds as ``message_content``; the array-of-parts
    shape folds each part's ``text`` as ``content_part_text`` in order. A content
    that is neither contributes no text. Each pair records ``index``.
    """
    content = message.get(_MESSAGE_CONTENT_KEY)
    if isinstance(content, str):
        pairs.append((_CHANNEL_MESSAGE_CONTENT, content))
        message_index.append(index)
        return
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get(_CONTENT_PART_TEXT_KEY)
            if isinstance(text, str):
                pairs.append((_CHANNEL_CONTENT_PART_TEXT, text))
                message_index.append(index)


def _fold_tool_calls(
    message: Mapping[str, object],
    index: int,
    pairs: list[tuple[str, str]],
    message_index: list[int],
) -> None:
    """Fold ``messages[].tool_calls[].function.arguments`` (R1.2).

    Each tool call's ``function.arguments`` string folds as ``tool_arguments`` in
    order; a call with no well-typed ``arguments`` contributes none. Each pair
    records ``index``.
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
            message_index.append(index)


def _fold_function_call(
    message: Mapping[str, object],
    index: int,
    pairs: list[tuple[str, str]],
    message_index: list[int],
) -> None:
    """Fold the legacy ``messages[].function_call.arguments`` (R1.2).

    The single legacy ``function_call`` object's ``arguments`` string folds as
    ``function_arguments``; a missing/mistyped ``arguments`` contributes none. Each
    pair records ``index``.
    """
    function_call = message.get(_MESSAGE_FUNCTION_CALL_KEY)
    if not isinstance(function_call, dict):
        return
    arguments = function_call.get(_FUNCTION_ARGUMENTS_KEY)
    if isinstance(arguments, str):
        pairs.append((_CHANNEL_FUNCTION_ARGUMENTS, arguments))
        message_index.append(index)


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
# Output budget: v1 clamp + prompt-char estimate (Group F, R2.15/R2.16).
# --------------------------------------------------------------------------- #
#
# ``clamp_output_budget`` reproduces v1's pre-estimate clamp so the v2 budget phase
# charges the SAME tokens as v1 (oracle ``tests/admit/_v1_token_estimate_oracle``).
# ``prompt_chars`` reproduces v1's folded-input-text length (one message's content
# parts join with a single separator). Both are PURE and take their bounds from the
# injected ``ResourceContract`` -- no capacity literal lives here.

#: The v1 ``request_clamps`` record order: ``n`` first, then ``max_tokens``.
_CLAMP_FIELD_N = "n"
_CLAMP_FIELD_MAX_TOKENS = "max_tokens"


def clamp_output_budget(
    req: CanonicalRequest, *, org_ceiling: int, contract: ResourceContract
) -> CanonicalRequest:
    """Clamp ``req``'s output budget to v1's bounds (Group F, R2.15/R2.16).

    Returns a new :class:`CanonicalRequest` (via :func:`dataclasses.replace`) with
    ``n' = min(req.n, contract.max_completions())`` and ``max_tokens' =
    min(req.max_tokens, min(org_ceiling, contract.max_output_tokens()))`` (v1's
    ``min(mt, org ceiling, hard cap)``), plus one ``"<field>=<pre>-><post>"``
    ``request_clamps`` record per CHANGED field in v1's order (``n`` then
    ``max_tokens``), matching the oracle strings (``"n=8->1"``,
    ``"max_tokens=1000000->4096"``). Idempotent; ``org_ceiling < 1`` raises
    :class:`ValueError`; ``max_completion_tokens`` is untouched (v1 charges only
    ``max_tokens``). PURE: bounds from the injected ``contract``, no literal here.
    """
    if org_ceiling < 1:
        raise ValueError(f"org_ceiling must be >= 1, got {org_ceiling}")
    clamps: list[str] = []
    new_n = min(req.n, contract.max_completions())
    if new_n != req.n:
        clamps.append(f"{_CLAMP_FIELD_N}={req.n}->{new_n}")
    max_tokens_ceiling = min(org_ceiling, contract.max_output_tokens())
    new_max_tokens = min(req.max_tokens, max_tokens_ceiling)
    if new_max_tokens != req.max_tokens:
        clamps.append(f"{_CLAMP_FIELD_MAX_TOKENS}={req.max_tokens}->{new_max_tokens}")
    return dataclasses.replace(
        req, n=new_n, max_tokens=new_max_tokens, request_clamps=tuple(clamps)
    )


def prompt_chars(req: CanonicalRequest) -> int:
    """Summed input-channel char count, v1 folded-input-text length (Group F).

    Sums ``len(text)`` over every ``req.input_channels`` pair, then adds one
    separator char for each ADDITIONAL part of the same message: for a maximal run
    of consecutive pairs sharing an ``input_message_index`` value, the run's joined
    length is ``sum(len(part)) + (count - 1)``, mirroring v1 joining a message's
    content parts with a single ``" "``. A single-part run adds no separator; an
    empty ``input_message_index`` yields the bare char sum. PURE wire logic.
    """
    total = sum(len(text) for _channel, text in req.input_channels)
    index = req.input_message_index
    separators = sum(1 for p in range(1, len(index)) if index[p] == index[p - 1])
    return total + separators


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
# The response content is EXACTLY the concatenation of the enforced output-content
# ``deltas`` text -- no mutation. ``json.dumps`` runs with ``ensure_ascii=True``
# (non-ASCII -> ``\uXXXX``, matching the conformance frames + ``sse.py``),
# ``separators=(",", ":")`` and a FIXED key order, so the same deltas always render
# the same bytes and ``json.loads`` recovers the content byte-for-byte.
#
# CHANNEL MAPPING. The transport ``channel_name`` is the shipped
# ``domain/channels.py`` spelling the output scan keys on (``output_content`` /
# ``output_reasoning`` / ``output_refusal`` / ``output_tool_arguments``); the
# default egress ``content`` delta key is treated as output content too, so a
# collected stream and a direct non-stream completion render the same content
# bytes. One output ``message`` is produced (the non-stream single ``choices[0]``).

#: The non-streaming SDK response ``object`` discriminator. The stock OpenAI SDK
#: parses a non-streamed body as a ``ChatCompletion`` only when this reads exactly
#: ``chat.completion`` (matches the recorded conformance body). A plain string.
COMPLETION_OBJECT = "chat.completion"

#: Defaults for the SDK-required ``id`` / ``created`` identity fields (the enforced
#: ``deltas`` carry only text, not stream identity; parity with ``sse.py``'s
#: ``DEFAULT_*``). Plain string/int, not capacity literals.
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
