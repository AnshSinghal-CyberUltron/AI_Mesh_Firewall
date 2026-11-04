"""SSE codec + state machine (GW12, task 5.1).

``edge/wire`` is the ONLY layer permitted to render SSE (the import-linter layer
order ``edge > admit > plan > detect > resolve > dispatch > egress > audit >
runtime > contracts > domain`` puts ``edge`` at the top; it may import
``egress`` for the shipped ``DownstreamFrame`` transport type and ``domain`` for
the ``STREAM_*`` posture codes). This module serialises an internal
``egress.stream.DownstreamFrame`` to the wire bytes an OpenAI SDK client parses,
and decodes an upstream SSE byte stream back into ``DownstreamFrame``s.

Three components:

* :class:`SSEEncoder` -- turns a :class:`DownstreamFrame` into a
  ``data: {json}\\n\\n`` content frame (R1.1), the terminal ``data: [DONE]\\n\\n``
  marker (R1.2), and a declared, SDK-parseable ``Error_Frame`` built from a
  posture ``STREAM_*`` code (R1.3). The encoder is a one-way state machine: once
  it has emitted ``done()`` or an ``error()`` frame it is *terminal* and refuses
  to emit any further content frame -- the "no content after error" contract of
  R1.3 enforced structurally rather than by convention.
* :class:`SSEDecoder` -- decodes an upstream SSE byte stream. It BUFFERS bytes
  until a complete event (terminated by the blank-line ``\\n\\n`` separator) has
  arrived, so a chunk boundary that splits a multi-byte UTF-8 sequence or a JSON
  ``\\uXXXX`` surrogate escape -- including a high/low surrogate PAIR split across
  two chunks -- is never decoded half-formed (R1.4). Because a complete event is
  the only thing ever decoded, the decode is CONFLUENT: the same concatenated
  byte stream yields the same code points regardless of where the fed-chunk
  boundaries fall (Property 9). An undecodable complete event sets the
  :meth:`SSEDecoder.malformed` latch so the caller fails closed with
  ``STREAM_MALFORMED_UPSTREAM`` and never reports the stream successful (R1.5).
* The value types :class:`SSEChunk`, :class:`SSEChoice`, :class:`SSEErrorFrame` (Data Models).

Round-trip (R1.6, Property 8 codec half): :meth:`SSEDecoder.feed` of a
well-formed content frame yields a ``DownstreamFrame`` whose ``text_deltas`` are
byte-identical to the frame the :class:`SSEEncoder` serialised, so
decode-then-encode reproduces an equivalent ``DownstreamFrame``.

**No capacity literal, no module-level mutable.** The one module-level table
(the ``STREAM_*`` code -> error-shape map) is a ``MappingProxyType``; the SDK
frame-shape constants are plain strings; nothing here holds a capacity-position
literal (the serving skin's bounds all live in ``runtime/resources.py``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from gateway_v2.domain import posture
from gateway_v2.edge.wire.sse_chunks import (
    CHUNK_OBJECT,
    DEFAULT_CREATED,
    ChunkEvent,
    SSEChoice,
    SSEChunk,
    StreamIdentity,
    ToolFragment,
)
from gateway_v2.edge.wire.sse_decode import SSEDecoder, trace_comment_payload
from gateway_v2.egress.stream import DownstreamFrame

__all__ = (
    "CHUNK_OBJECT",
    "DEFAULT_CREATED",
    "DEFAULT_MODEL",
    "DEFAULT_STREAM_ID",
    "DONE_MARKER",
    "ERROR_SHAPES",
    "SSEChoice",
    "SSEChunk",
    "SSEDecoder",
    "SSEEncoder",
    "SSEEncoderTerminated",
    "SSEErrorFrame",
    "StreamIdentity",
    "TRACE_COMMENT_PREFIX",
    "TRACE_EVENT",
    "trace_comment_payload",
)


# --------------------------------------------------------------------------- #
# SDK wire-shape constants (plain strings -- not capacity literals).
# --------------------------------------------------------------------------- #

#: The terminal marker that ends an OpenAI streaming response. Bytes are fixed;
#: the SDK stops iterating cleanly when it reads this sentinel (R1.2).
DONE_MARKER = b"data: [DONE]\n\n"

#: The token that marks the gateway's NON-CONTENT trace frame (GW13, C5). Since GW34 V13 (owner
#: decision, 4 Nov 2026) the trace is an SSE COMMENT line, ``: amf.trace {json}``: every compliant
#: SSE parser (the Python and Node ``openai`` SDKs included) skips a comment, so the trace never
#: reaches an SDK iterator. Trace consumers (the console, the Responses translator, the decoder)
#: read the comment explicitly. A frame-shape marker string, not a capacity-position literal.
TRACE_EVENT = "amf.trace"

#: The exact bytes a trace comment line starts with (``:`` then the marker, then one space).
TRACE_COMMENT_PREFIX = b": " + TRACE_EVENT.encode("ascii") + b" "

#: Defaults used to populate the SDK-required ``id`` / ``model`` / ``created``
#: fields when the caller does not override them. ``DownstreamFrame`` carries
#: only ``text_deltas`` + ``error_code`` (it is the shipped egress transport type
#: and is NOT redefined here), so the stream identity is supplied to the encoder
#: once at construction rather than per frame.
DEFAULT_STREAM_ID = "chatcmpl-stream"
DEFAULT_MODEL = "gateway"


# --------------------------------------------------------------------------- #
# Declared Error_Frame shapes (R1.3): posture STREAM_* code -> SDK error body.
# --------------------------------------------------------------------------- #

# The OpenAI SDKs parse a terminal error as a top-level ``{"error": {...}}``
# object with ``message`` / ``type`` / ``code`` string fields (the shape the
# recorded conformance stub emits for a mid-stream block). Each posture code
# maps to exactly ONE declared ``(error_type, message)`` so the two sides of the
# stream cannot disagree on spelling, and ``code`` echoes the posture code
# verbatim. A code with no declared shape renders the generic shape below rather
# than leaking the raw code string as a message (parity with the unmapped-code
# fallback the Error_Envelope renders at the HTTP layer).

_GENERIC_ERROR_TYPE = "gateway_error"
_GENERIC_ERROR_MESSAGE = "The stream was terminated by the gateway."

ERROR_SHAPES: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        posture.STREAM_INTER_CHUNK_TIMEOUT: (
            "timeout_error",
            "The upstream stalled between chunks and the stream was terminated.",
        ),
        posture.STREAM_IDLE_TIMEOUT: (
            "timeout_error",
            "The downstream client went idle and the stream was terminated.",
        ),
        posture.STREAM_WRITE_TIMEOUT: (
            "timeout_error",
            "A downstream write blocked too long and the stream was terminated.",
        ),
        posture.STREAM_MAX_DURATION: (
            "timeout_error",
            "The stream exceeded the maximum permitted duration and was terminated.",
        ),
        posture.STREAM_KEY_REVOKED: (
            "authentication_error",
            "The API key was revoked and the stream was terminated.",
        ),
        posture.INVALID_API_KEY: (
            "invalid_request_error",
            "Incorrect API key provided. Pass a valid key as 'Authorization: Bearer <key>'.",
        ),
        posture.STREAM_PLAN_CHANGED: (
            "gateway_error",
            "The plan changed mid-stream and the stream was terminated.",
        ),
        posture.STREAM_SNAPSHOT_STALE: (
            "gateway_error",
            "A control-plane snapshot was stale; the stream was terminated fail-closed.",
        ),
        posture.STREAM_MALFORMED_UPSTREAM: (
            "gateway_error",
            "The upstream response could not be decoded and the stream was terminated.",
        ),
        posture.STREAM_BUFFER_UNAVAILABLE: (
            "gateway_error",
            "No buffer capacity was available and the stream was rejected.",
        ),
        posture.REQUEST_ID_UNAVAILABLE: (
            "service_unavailable",
            "The request could not be started.",
        ),
        posture.KILL_SWITCH_ENGAGED: (
            "service_unavailable",
            "An incident kill switch is engaged; the request was refused.",
        ),
        posture.MODEL_KILLED: (
            "service_unavailable",
            "The requested model is disabled by an incident kill switch.",
        ),
        posture.MODEL_ISOLATED_NO_FALLBACK: (
            "service_unavailable",
            "The requested model is isolated and no eligible fallback exists.",
        ),
        posture.REROUTE_REFUSED: (
            "service_unavailable",
            "The requested model is isolated and this surface cannot be rerouted.",
        ),
        posture.STREAM_MODEL_ISOLATED: (
            "service_unavailable",
            "The model was isolated and the stream was terminated.",
        ),
        posture.STREAM_STRICT_BUFFER_OVERFLOW: (
            "gateway_error",
            "The strict-withhold buffer limit was exceeded and the response was withheld.",
        ),
        # Pre-existing terminal codes already raised by the shipped pipeline
        # (`egress/stream.py`): reused unchanged so a cut / scan-failure / block
        # that reaches the wire renders a declared error, not the generic shape.
        "stream_killed": (
            "gateway_error",
            "The stream was terminated by an in-flight control decision.",
        ),
        "scan_failure": (
            "gateway_error",
            "The output guard could not complete and the stream was terminated.",
        ),
        "output_blocked": (
            "gateway_error",
            "The response was withheld by the output guard.",
        ),
        # The INPUT content block (GW15/GW16, openai-conformance-gw01-local §10.2.2):
        # the HTTP ``Error_Envelope`` renders this as 400 ``invalid_request_error`` (the
        # frozen OpenAI content-block type) with the v2 posture ``code`` ``input_blocked``;
        # the SSE frame carries the SAME ``type`` + ``code`` so a streaming block and a
        # non-streaming block never disagree on the wire shape (R1.3 / R3.4 parity).
        "input_blocked": (
            "invalid_request_error",
            "The request was blocked by an input content policy.",
        ),
        # The INPUT unmaskable REDACT refusal (v2-enforcement-leaks,
        # edge/input_enforcement.py ``REFUSE_INPUT_UNMASKABLE``): a REDACT whose
        # outbound bytes cannot be verified (a detected value survives, or the
        # transformation cannot be applied). The HTTP ``Error_Envelope`` renders it
        # 400 ``invalid_request_error`` with the v2 ``code`` ``input_unmaskable`` --
        # the same content-refusal family as ``input_blocked`` -- so a streaming
        # unmaskable refusal renders this DECLARED frame, not the generic one, and
        # the two sides agree on ``type`` + ``code`` (R1.3 / R3.4 parity).
        "input_unmaskable": (
            "invalid_request_error",
            "The request carried content that could not be masked and was withheld.",
        ),
        "policy_regex_timeout": (
            "invalid_request_error",
            "A policy pattern could not be evaluated and the request was withheld.",
        ),
        # The Group F output-budget parse refusals (v1 boundary validator): a
        # ``max_tokens`` / ``max_completion_tokens`` above the parse boundary. The
        # HTTP ``Error_Envelope`` renders each 400 ``invalid_request_error`` with the
        # v2 ``code``; the human message stays BOUNDARY-FREE here because the boundary
        # value is a capacity position (``runtime/resources.py`` only) and must not be
        # a literal in this module. The concrete boundary is appended at the
        # contract-bearing HTTP render site (``edge/errors.render_http`` ``detail``,
        # supplied by ``ChatRoute._render_refusal`` from
        # ``contract.max_output_tokens_boundary()``); a streaming boundary refusal
        # keeps this frozen boundary-free frame shape.
        "invalid_max_tokens": (
            "invalid_request_error",
            "The requested max_tokens exceeds the permitted maximum.",
        ),
        "invalid_max_completion_tokens": (
            "invalid_request_error",
            "The requested max_completion_tokens exceeds the permitted maximum.",
        ),
        # GW15 /v1/responses adapter codes (edge/wire/responses.py, edge/openai_responses.py).
        # Chat does not emit them.
        "responses_malformed_body": (
            "invalid_request_error",
            "The request body must be a JSON object.",
        ),
        "responses_invalid_model": (
            "invalid_request_error",
            "The model field is required and must be a string.",
        ),
        "responses_missing_input": (
            "invalid_request_error",
            "The request must include input or instructions.",
        ),
        "unsupported_content_part": (
            "invalid_request_error",
            "The request contains a content part this gateway cannot scan.",
        ),
        "response_not_found": (
            "not_found_error",
            "No stored response with that id exists for this organization.",
        ),
        "response_store_unavailable": (
            "service_unavailable",
            "The response could not be stored and was not returned as stored.",
        ),
        # GW17 MCP surface codes (edge/mcp.py, dispatch/mcp_broker.py).
        "mcp_server_not_found": (
            "not_found_error",
            "No active MCP server with that name exists for this organization.",
        ),
        "mcp_tool_disabled": ("permission_error", "That tool is not enabled on this server."),
        "mcp_upstream_auth_unsupported": (
            "permission_error",
            "This MCP server needs credentials the gateway does not forward.",
        ),
        "mcp_destination_refused": (
            "permission_error",
            "The MCP server's address or command is not allowed.",
        ),
        "mcp_org_unmapped": (
            "permission_error",
            "This organization is not configured for MCP on this gateway.",
        ),
        "mcp_registry_unavailable": (
            "service_unavailable",
            "The MCP server registry could not be reached.",
        ),
        "mcp_broker_unavailable": (
            "service_unavailable",
            "The MCP sandbox could not be reached.",
        ),
        "mcp_upstream_error": ("gateway_error", "The MCP server returned an unusable reply."),
        "mcp_request_too_large": (
            "invalid_request_error",
            "The MCP request exceeds a size, depth, or node limit.",
        ),
        "mcp_result_too_large": (
            "gateway_error",
            "The MCP reply exceeds a size, depth, node, or block limit and was withheld.",
        ),
        "mcp_parse_error": ("invalid_request_error", "The request body is not valid JSON."),
        "mcp_invalid_request": (
            "invalid_request_error",
            "The request is not a single valid JSON-RPC 2.0 message.",
        ),
        "mcp_method_not_found": ("not_found_error", "That MCP method is not supported."),
        "mcp_invalid_params": (
            "invalid_request_error",
            "The method parameters have the wrong shape.",
        ),
        "endpoint_not_found": ("not_found_error", "No endpoint is served at this path."),
        "unsupported_parameter": (
            "invalid_request_error",
            "The request carries a parameter this gateway does not forward.",
        ),
        # GW36 S1: terminal failures the gateway detected on the upstream call. Never provider
        # text: the provider's own message is not echoed.
        "upstream_invalid_request": (
            "invalid_request_error",
            "The model provider rejected the request.",
        ),
        "upstream_rate_limited": ("rate_limit_error", "The model provider is rate limiting."),
        "upstream_not_found": ("not_found_error", "The model provider does not serve that model."),
        "upstream_timeout": ("timeout_error", "The model provider did not answer in time."),
        "upstream_auth_error": (
            "gateway_error",
            "The gateway's credential for the model provider was refused.",
        ),
        "upstream_unavailable": ("gateway_error", "The model provider could not be reached."),
        "upstream_error": ("gateway_error", "The model provider returned an error."),
        "upstream_request_unbuildable": (
            "gateway_error",
            "The gateway could not build the request for the model provider.",
        ),
        "method_not_allowed": (
            "invalid_request_error",
            "This endpoint does not accept that HTTP method.",
        ),
        # GW28 multi-model routing: terminal refusals the gateway decided before/while routing.
        "routing_unknown_model": ("not_found_error", "The requested model is not served."),
        "routing_no_candidates": ("service_unavailable", "No model provider is configured."),
        "routing_all_unavailable": ("service_unavailable", "Every model provider is unavailable."),
        "routing_attempts_exhausted": ("service_unavailable", "Every provider attempt failed."),
        "routing_teardown_unconfirmed": ("service_unavailable", "The request was stopped."),
        "routing_context_missing": ("service_unavailable", "The request could not be routed."),
        "routing_snapshot_unavailable": ("service_unavailable", "Provider health is unknown."),
        "routing_internal_error": ("service_unavailable", "The request could not be routed."),
    }
)


# --------------------------------------------------------------------------- #
# Value types (Data Models). ``SSEChoice`` / ``SSEChunk`` live in ``sse_chunks``.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SSEErrorFrame:
    """The declared, SDK-parseable error body rendered from a posture code (R1.3).

    Serialised as a top-level ``{"error": {"message", "type", "code"}}`` object --
    the shape the official SDKs decode into a typed error. ``code`` echoes the
    posture ``STREAM_*`` code so the trace keeps the single spelling.
    """

    error_type: str
    code: str
    message: str

    def as_json_obj(self) -> dict[str, object]:
        """The plain JSON object the encoder serialises for an error frame."""
        return {
            "error": {
                "message": self.message,
                "type": self.error_type,
                "code": self.code,
            }
        }


def error_frame_for(error_code: str) -> SSEErrorFrame:
    """Build the declared :class:`SSEErrorFrame` for a posture ``STREAM_*`` code.

    A code with a declared shape in :data:`ERROR_SHAPES` renders that shape; an
    unmapped code renders the generic shape (never leaking the raw code as the
    message), mirroring the Error_Envelope's unmapped-code fallback (R1.3 /
    R3.4). ``code`` always echoes the originating code verbatim.
    """
    shape = ERROR_SHAPES.get(error_code)
    if shape is None:
        return SSEErrorFrame(
            error_type=_GENERIC_ERROR_TYPE,
            code=error_code,
            message=_GENERIC_ERROR_MESSAGE,
        )
    error_type, message = shape
    return SSEErrorFrame(error_type=error_type, code=error_code, message=message)


def _tool_fragment_json(fragment: ToolFragment) -> dict[str, object]:
    """One ``delta.tool_calls[]`` entry; the first fragment carries ``id`` / ``type`` / ``name``."""
    entry: dict[str, object] = {"index": fragment.index}
    if fragment.id is not None:
        entry["id"] = fragment.id
        entry["type"] = "function"
    function: dict[str, object] = {}
    if fragment.name is not None:
        function["name"] = fragment.name
    if fragment.arguments is not None:
        function["arguments"] = fragment.arguments
    if function:
        entry["function"] = function
    return entry


# --------------------------------------------------------------------------- #
# Encoder (R1.1 / R1.2 / R1.3).
# --------------------------------------------------------------------------- #


class SSEEncoderTerminated(Exception):
    """A content frame was requested after the encoder emitted a terminal frame.

    Enforces the R1.3 "no content frame after an error" contract (and the
    symmetric "no content after ``[DONE]``") at the state-machine level: once
    :meth:`SSEEncoder.error` or :meth:`SSEEncoder.done` has run, the encoder is
    terminal and :meth:`SSEEncoder.content` raises rather than emitting bytes
    that would follow a terminal frame on the wire.
    """


class SSEEncoder:
    """Serialise ``DownstreamFrame``s to SDK-parseable SSE wire bytes.

    One encoder instance serves one downstream stream. The stream identity
    (``id`` / ``model`` / ``created``) is fixed at construction because the
    shipped ``DownstreamFrame`` carries only ``text_deltas`` + ``error_code``.
    The encoder is a one-way state machine: :attr:`terminated` flips the first
    time :meth:`done` or :meth:`error` runs, after which :meth:`content` refuses
    to emit (R1.3).
    """

    __slots__ = ("_created", "_index", "_model", "_role_sent", "_stream_id", "_terminated")

    def __init__(
        self,
        *,
        stream_id: str = DEFAULT_STREAM_ID,
        model: str = DEFAULT_MODEL,
        created: int = DEFAULT_CREATED,
        identity: StreamIdentity | None = None,
    ) -> None:
        """``identity`` (GW34) fixes ``id`` / ``model`` / ``created`` for every chunk of the
        stream; without it the legacy keyword defaults apply."""
        if identity is not None:
            stream_id, model, created = identity.stream_id, identity.model, identity.created
        self._stream_id = stream_id
        self._model = model
        self._created = created
        self._index = 0
        self._terminated = False
        self._role_sent = False

    @property
    def terminated(self) -> bool:
        """Whether a terminal frame (``[DONE]`` or an error) has been emitted."""
        return self._terminated

    def content(self, frame: DownstreamFrame) -> bytes:
        """Serialise a content ``DownstreamFrame`` as ``data: {json}\\n\\n`` (R1.1).

        Each ``(channel, text)`` delta becomes one ``choices[]`` entry whose
        ``delta`` object carries the released text under ``content`` (the
        ``channel`` names the OpenAI delta key when it is not the default
        ``content`` channel). The JSON body is the ``chat.completion.chunk`` shape
        the stock SDK parses.

        If ``frame.error_code`` is set, this is NOT a content frame -- the caller
        must route it through :meth:`error`; raising here keeps a terminal frame
        from being mis-serialised as content. Raises
        :class:`SSEEncoderTerminated` if the encoder has already emitted a
        terminal frame (R1.3 no-content-after-error contract).
        """
        if self._terminated:
            raise SSEEncoderTerminated(
                "content requested after a terminal frame was emitted",
            )
        if frame.error_code is not None:
            raise SSEEncoderTerminated(
                "content frame carries an error_code; route it through error()",
            )
        chunk = SSEChunk(
            id=self._stream_id,
            model=self._model,
            created=self._created,
            choices=tuple(self._choices_for(frame)),
        )
        return self._data_frame(chunk.as_json_obj())

    def chunk(self, event: ChunkEvent) -> bytes:
        """Serialise one merged :class:`ChunkEvent` as ONE ``choices[0]`` chunk (GW34, R5).

        All of the event's deltas share one ``delta`` object; ``delta.role`` is added to the FIRST
        chunk only; ``finish_reason`` is ``null`` unless the event carries one; the stream
        ``id`` / ``model`` / ``created`` are the encoder's fixed identity; no ``usage`` (D10).
        Refused after a terminal frame (R1.3).
        """
        if self._terminated:
            raise SSEEncoderTerminated("chunk requested after a terminal frame was emitted")
        delta: dict[str, object] = {}
        if not self._role_sent:
            delta["role"] = "assistant"
            self._role_sent = True
        if event.content:
            delta["content"] = event.content
        if event.reasoning:
            delta["reasoning_content"] = event.reasoning
        if event.refusal:
            delta["refusal"] = event.refusal
        for channel, text in event.extra:
            delta[channel] = delta.get(channel, "") + text  # type: ignore[operator]
        if event.tool_calls:
            delta["tool_calls"] = [_tool_fragment_json(f) for f in event.tool_calls]
        choice = SSEChoice(index=0, delta=delta, finish_reason=event.finish_reason)
        chunk = SSEChunk(
            id=self._stream_id, model=self._model, created=self._created, choices=(choice,)
        )
        return self._data_frame(chunk.as_json_obj())

    def finish(self, reason: str) -> bytes:
        """The final chunk: an empty ``delta`` and the stream's ``finish_reason`` (GW34, R5)."""
        if self._terminated:
            raise SSEEncoderTerminated("finish requested after a terminal frame was emitted")
        choice = SSEChoice(index=0, delta={}, finish_reason=reason)
        chunk = SSEChunk(
            id=self._stream_id, model=self._model, created=self._created, choices=(choice,)
        )
        return self._data_frame(chunk.as_json_obj())

    def done(self) -> bytes:
        """Emit the terminal ``data: [DONE]\\n\\n`` marker (R1.2).

        Flips the encoder terminal, so a later :meth:`content` raises. Idempotent
        in effect: calling it only re-returns the fixed marker bytes.
        """
        self._terminated = True
        return DONE_MARKER

    def trace(self, fields: Mapping[str, object]) -> bytes:
        """Emit a NON-CONTENT ``amf.trace`` meta frame carrying ``fields`` (GW13, C5; GW34 V13).

        Serialises ``fields`` (the Produced_Mode stamp plus the console object) as ONE SSE
        COMMENT line, ``: amf.trace {json}\\n\\n``. The JSON is compact, ASCII-only and so never
        contains a newline. A comment is skipped by every compliant SSE parser, so no SDK iterator
        ever sees the trace (R1.6); the gateway's own consumers read the comment explicitly.

        This is TRACE, not content, so it does NOT flip the encoder terminal and is NOT blocked by
        the ``terminated`` state (it is emitted just before ``[DONE]`` and, after an error frame,
        following it). ``done()`` / ``error()`` still refuse any subsequent :meth:`content`.
        """
        body = json.dumps(dict(fields), ensure_ascii=True, separators=(",", ":"))
        return TRACE_COMMENT_PREFIX + body.encode("ascii") + b"\n\n"

    def error(self, error_code: str) -> bytes:
        """Emit a declared ``Error_Frame`` for a posture code and go terminal (R1.3).

        Builds the SDK-parseable ``{"error": {...}}`` body via
        :func:`error_frame_for`, serialises it as ``data: {json}\\n\\n``, and
        flips the encoder terminal so no content frame can follow. An unmapped
        code renders the generic error shape rather than leaking the raw code.
        """
        self._terminated = True
        err = error_frame_for(error_code)
        return self._data_frame(err.as_json_obj())

    def _choices_for(self, frame: DownstreamFrame) -> list[SSEChoice]:
        """Map a frame's ``text_deltas`` to ONE SDK ``choices[0]`` entry (GW34, R5).

        All deltas of a frame share one ``delta`` object (n is forced to 1, D17), each channel
        under its own delta key with same-channel text concatenated in order, so a stock SDK reads
        one choice per chunk. A frame with no delta yields an empty ``choices`` list.
        """
        delta: dict[str, object] = {}
        for channel, text in frame.text_deltas:
            delta[channel] = str(delta.get(channel, "")) + text
        if not delta:
            return []
        return [SSEChoice(index=0, delta=delta)]

    def _data_frame(self, obj: Mapping[str, object]) -> bytes:
        """Serialise a JSON object as a single ``data: {json}\\n\\n`` SSE frame.

        ``ensure_ascii=True`` escapes every non-ASCII code point as a JSON
        ``\\uXXXX`` escape (a surrogate PAIR for an astral code point), so the
        wire bytes stay 7-bit ASCII and a decoder on the far side exercises the
        split-surrogate buffering path. ``separators`` drops insignificant
        whitespace so the body is compact and stable.
        """
        body = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
        return b"data: " + body.encode("ascii") + b"\n\n"
