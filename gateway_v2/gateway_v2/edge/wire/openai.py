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
from types import MappingProxyType

from gateway_v2.domain.plan import Surface
from gateway_v2.edge.wire.openai_codes import (
    INVALID_MAX_COMPLETION_TOKENS,
    INVALID_MAX_TOKENS,
    MALFORMED_BODY,
    MALFORMED_MISSING_MODEL,
    MALFORMED_NOT_AN_OBJECT,
    MALFORMED_WRONG_SHAPE,
    UNSUPPORTED_CONTENT_PART,
    UNSUPPORTED_PARAMETER,
    MalformedRequest,
)
from gateway_v2.edge.wire.openai_fold import (
    ChannelFoldError,
    FoldResult,
    UpstreamTemplate,
    fold_input_channels,
    fold_request,
)
from gateway_v2.edge.wire.openai_params import forwarded_fields
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "INVALID_MAX_COMPLETION_TOKENS",
    "INVALID_MAX_TOKENS",
    "MALFORMED_BODY",
    "MALFORMED_MISSING_MODEL",
    "MALFORMED_NOT_AN_OBJECT",
    "MALFORMED_WRONG_SHAPE",
    "UNSUPPORTED_CONTENT_PART",
    "UNSUPPORTED_PARAMETER",
    "CanonicalRequest",
    "ChannelFoldError",
    "FoldResult",
    "MalformedRequest",
    "OpenAIRequestParser",
    "OpenAIResponseRenderer",
    "UpstreamTemplate",
    "clamp_output_budget",
    "fold_input_channels",
    "prompt_chars",
)


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

    GW36 S1 adds ``upstream_template`` (the forwardable structure with each scanned
    text occurrence as a slot, built by the same loop as ``input_channels``) and
    ``forwarded`` (the validated top-level fields copied to the provider as given).
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
    upstream_template: UpstreamTemplate | None = None
    forwarded: Mapping[str, object] = MappingProxyType({})


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
        forwarded = forwarded_fields(obj)
        fold, template = fold_request(obj)
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
            upstream_template=template,
            forwarded=forwarded,
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
