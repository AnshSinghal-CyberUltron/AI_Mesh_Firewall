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
* The value types :class:`SSEChunk`, :class:`SSEChoice`, :class:`SSEErrorFrame`
  -- the SDK-shaped content body and the declared error body (Data Models).

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
)


# --------------------------------------------------------------------------- #
# SDK wire-shape constants (plain strings -- not capacity literals).
# --------------------------------------------------------------------------- #

#: The ``object`` discriminator every streamed content frame carries. The stock
#: OpenAI SDK parses a streamed frame as a ``ChatCompletionChunk`` only when this
#: field reads exactly ``chat.completion.chunk`` (matches the recorded
#: conformance frame in ``tests/openai_conformance/test_openai_sdk_compat.py``).
CHUNK_OBJECT = "chat.completion.chunk"

#: The terminal marker that ends an OpenAI streaming response. Bytes are fixed;
#: the SDK stops iterating cleanly when it reads this sentinel (R1.2).
DONE_MARKER = b"data: [DONE]\n\n"

#: Defaults used to populate the SDK-required ``id`` / ``model`` / ``created``
#: fields when the caller does not override them. ``DownstreamFrame`` carries
#: only ``text_deltas`` + ``error_code`` (it is the shipped egress transport type
#: and is NOT redefined here), so the stream identity is supplied to the encoder
#: once at construction rather than per frame.
DEFAULT_STREAM_ID = "chatcmpl-stream"
DEFAULT_MODEL = "gateway"
DEFAULT_CREATED = 0


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
    }
)


# --------------------------------------------------------------------------- #
# Value types (Data Models).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SSEChoice:
    """One SDK ``choices[]`` entry: an index, a ``delta`` object, a finish reason.

    ``delta`` is a read-only ``Mapping`` (e.g. ``{"content": "Hello"}`` or
    ``{"role": "assistant", "content": "Hello"}``) carried straight into the
    serialised JSON. ``finish_reason`` is ``None`` on every content chunk and set
    (``"stop"``) only on the terminal content chunk, matching the SDK schema.
    """

    index: int
    delta: Mapping[str, object]
    finish_reason: str | None = None

    def as_json_obj(self) -> dict[str, object]:
        """The plain JSON object for this choice (SDK ``choices[]`` entry)."""
        return {
            "index": self.index,
            "delta": dict(self.delta),
            "finish_reason": self.finish_reason,
        }


@dataclass(frozen=True, slots=True)
class SSEChunk:
    """The SDK-shaped content frame body serialised to ``data: {json}\\n\\n``.

    Carries the OpenAI ``chat.completion.chunk`` fields the stock SDK requires to
    parse a streamed frame: ``id`` / ``object`` / ``created`` / ``model`` and the
    ``choices`` list. Frozen + slotted; ``choices`` is a tuple so the value type
    is immutable.
    """

    id: str
    model: str
    choices: tuple[SSEChoice, ...]
    created: int = DEFAULT_CREATED

    def as_json_obj(self) -> dict[str, object]:
        """The plain JSON object the encoder serialises for a content frame."""
        return {
            "id": self.id,
            "object": CHUNK_OBJECT,
            "created": self.created,
            "model": self.model,
            "choices": [choice.as_json_obj() for choice in self.choices],
        }


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

    __slots__ = ("_created", "_index", "_model", "_stream_id", "_terminated")

    def __init__(
        self,
        *,
        stream_id: str = DEFAULT_STREAM_ID,
        model: str = DEFAULT_MODEL,
        created: int = DEFAULT_CREATED,
    ) -> None:
        self._stream_id = stream_id
        self._model = model
        self._created = created
        self._index = 0
        self._terminated = False

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

    def done(self) -> bytes:
        """Emit the terminal ``data: [DONE]\\n\\n`` marker (R1.2).

        Flips the encoder terminal, so a later :meth:`content` raises. Idempotent
        in effect: calling it only re-returns the fixed marker bytes.
        """
        self._terminated = True
        return DONE_MARKER

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
        """Map a frame's ``text_deltas`` to SDK ``choices[]`` entries.

        The default ``content`` channel maps to the ``content`` delta key; any
        other channel name is used as the delta key verbatim so a non-default
        text stream (e.g. ``reasoning_content``) round-trips through the SDK
        delta object. One choice per delta, enumerated by its position.
        """
        choices: list[SSEChoice] = []
        for position, (channel, text) in enumerate(frame.text_deltas):
            key = "content" if channel == "content" else channel
            choices.append(SSEChoice(index=position, delta={key: text}))
        return choices

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


# --------------------------------------------------------------------------- #
# Decoder (R1.4 / R1.5 / R1.6, Property 9).
# --------------------------------------------------------------------------- #


class SSEDecoder:
    """Decode an upstream SSE byte stream into ``DownstreamFrame``s.

    :meth:`feed` accumulates raw bytes and only decodes a COMPLETE event -- one
    terminated by the blank-line ``\\n\\n`` separator. Any trailing partial event
    (and therefore any split multi-byte UTF-8 sequence or split JSON
    ``\\uXXXX`` surrogate escape, including a high/low surrogate PAIR split across
    two chunks) stays in the internal buffer until its remaining bytes arrive
    (R1.4). Decoding only complete events makes the decode CONFLUENT: the same
    concatenated byte stream yields the same frames regardless of where the fed
    boundaries fall (Property 9).

    The terminal ``[DONE]`` marker yields no frame (it signals clean termination,
    not content). A ``{"error": {...}}`` event yields a terminal
    ``DownstreamFrame`` whose ``error_code`` is the event's ``code``. A complete
    event that cannot be decoded (bad UTF-8, non-JSON data, or an unrecognised
    shape) sets the :meth:`malformed` latch and yields no frame, so the caller
    fails closed with ``STREAM_MALFORMED_UPSTREAM`` and never reports success
    (R1.5).
    """

    __slots__ = ("_buffer", "_malformed")

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._malformed = False

    def feed(self, raw: bytes) -> tuple[DownstreamFrame, ...]:
        """Decode every COMPLETE event in the buffer; buffer any partial tail.

        Appends ``raw`` to the internal buffer, splits off each event terminated
        by ``\\n\\n``, and decodes each in order. A partial trailing event stays
        buffered for the next :meth:`feed`, so a chunk boundary never splits a
        decoded code point (R1.4). Returns the frames decoded this call (possibly
        empty); ``[DONE]`` and malformed events contribute no frame.
        """
        self._buffer.extend(raw)
        frames: list[DownstreamFrame] = []
        for event_bytes in self._drain_complete_events():
            frame = self._decode_event(event_bytes)
            if frame is not None:
                frames.append(frame)
        return tuple(frames)

    def malformed(self) -> bool:
        """Whether an undecodable complete event has been seen (R1.5).

        One-way latch: once set it stays set, so the caller terminates the stream
        with ``STREAM_MALFORMED_UPSTREAM`` and never reports the stream
        successful. A split event that is merely still buffered does NOT set this
        -- only a COMPLETE event that fails to decode does.
        """
        return self._malformed

    def _drain_complete_events(self) -> list[bytes]:
        """Pop each ``\\n\\n``-terminated event off the buffer, in order.

        Leaves a trailing partial event (no terminator yet) in the buffer. The
        event bytes returned exclude the ``\\n\\n`` separator.
        """
        events: list[bytes] = []
        sep = b"\n\n"
        while True:
            idx = self._buffer.find(sep)
            if idx == -1:
                break
            event = bytes(self._buffer[:idx])
            del self._buffer[: idx + len(sep)]
            if event:
                events.append(event)
        return events

    def _decode_event(self, event_bytes: bytes) -> DownstreamFrame | None:
        """Decode one complete event's bytes into a frame (or ``None``).

        Decodes the WHOLE event's bytes as UTF-8 in one step -- never a partial
        multi-byte prefix, because only complete events reach here -- so a split
        code point is reassembled before decoding (R1.4). Extracts the ``data:``
        payload, treats ``[DONE]`` as clean termination (no frame), JSON-parses
        the rest, and maps the recognised shapes to a ``DownstreamFrame``. Any
        decode or shape failure sets :meth:`malformed` and yields ``None`` (R1.5).
        """
        try:
            text = event_bytes.decode("utf-8")
        except UnicodeDecodeError:
            self._malformed = True
            return None
        payload = self._data_payload(text)
        if payload is None:
            self._malformed = True
            return None
        if payload == "[DONE]":
            return None
        return self._frame_from_json(payload)

    def _data_payload(self, event_text: str) -> str | None:
        """Join the ``data:`` field value(s) of an SSE event, or ``None``.

        An SSE event is CR/LF-delimited fields; the decoder reads the ``data:``
        field(s) and ignores comments (``:`` prefix) and other field names. Per
        the SSE spec a single leading space after the colon is stripped, and
        multiple ``data:`` lines join with a newline. Returns ``None`` when the
        event carries no ``data:`` field at all (an undecodable upstream shape).
        """
        data_lines: list[str] = []
        has_data = False
        for line in event_text.split("\n"):
            line = line.rstrip("\r")
            if not line or line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if field != "data":
                continue
            has_data = True
            data_lines.append(value[1:] if value.startswith(" ") else value)
        if not has_data:
            return None
        return "\n".join(data_lines)

    def _frame_from_json(self, payload: str) -> DownstreamFrame | None:
        """Parse a JSON ``data:`` payload into a ``DownstreamFrame`` (or ``None``).

        A ``{"error": {...}}`` object becomes a terminal frame carrying the
        error ``code`` (R1.3 decode side). A ``chat.completion.chunk`` object's
        ``choices[].delta`` content becomes the frame's ``text_deltas`` (R1.6
        round-trip). A payload that is not valid JSON, or that matches neither
        shape, sets :meth:`malformed` and yields ``None`` (R1.5).
        """
        try:
            obj = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            self._malformed = True
            return None
        if not isinstance(obj, dict):
            self._malformed = True
            return None
        error = obj.get("error")
        if isinstance(error, dict):
            return self._error_frame(error)
        deltas = self._deltas_from_choices(obj)
        if deltas is None:
            self._malformed = True
            return None
        return DownstreamFrame(text_deltas=deltas)

    def _error_frame(self, error: Mapping[str, object]) -> DownstreamFrame:
        """A terminal ``DownstreamFrame`` from an upstream ``{"error": {...}}``.

        ``error_code`` is the error object's ``code`` when it is a string, else
        ``STREAM_MALFORMED_UPSTREAM`` (an error frame without a usable code is
        still terminal and still fails closed). No content deltas accompany a
        terminal error frame.
        """
        code = error.get("code")
        error_code = code if isinstance(code, str) and code else posture.STREAM_MALFORMED_UPSTREAM
        return DownstreamFrame(text_deltas=(), error_code=error_code)

    def _deltas_from_choices(
        self, obj: Mapping[str, object]
    ) -> tuple[tuple[str, str], ...] | None:
        """Extract ``(channel, text)`` deltas from a chunk object's ``choices``.

        Reads each ``choices[].delta`` object: a ``content`` string maps to the
        ``content`` channel, and any other string-valued delta key maps to a
        channel of that name, so the encoder's channel->delta-key mapping
        round-trips (R1.6). A missing/empty ``choices`` list yields an empty
        tuple (a valid content frame carrying no delta). Returns ``None`` when
        ``choices`` is present but not a list of delta objects (unrecognised
        shape -> malformed).
        """
        choices = obj.get("choices")
        if choices is None:
            return ()
        if not isinstance(choices, list):
            return None
        deltas: list[tuple[str, str]] = []
        for choice in choices:
            if not isinstance(choice, dict):
                return None
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                return None
            for key, value in delta.items():
                if isinstance(value, str):
                    channel = "content" if key == "content" else key
                    deltas.append((channel, value))
        return tuple(deltas)
