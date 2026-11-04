"""The upstream SSE decoder, split out of `edge/wire/sse.py` (size gate; a pure move).

`edge/wire/sse.py` re-exports :class:`SSEDecoder`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from gateway_v2.domain import posture
from gateway_v2.egress.stream import DownstreamFrame

__all__ = ("FINISH_REASONS", "SSEDecoder")

#: The closed set of ``finish_reason`` values a stream may carry. The deprecated
#: ``function_call`` reason is rejected on purpose (D1b).
FINISH_REASONS = frozenset({"stop", "length", "tool_calls", "content_filter"})
_FINISH_CHANNEL = "finish_reason"
_TOOL_ID = "output_tool_id"
_TOOL_NAME = "output_tool_name"
_TOOL_ARGUMENTS = "output_tool_arguments"
_TOOL_CHANNEL_PREFIX = "output_tool_"
_KEY_MARK = "#"


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

    __slots__ = ("_buffer", "_done", "_first_id", "_first_name", "_malformed", "_max_tool_calls")

    def __init__(self, max_tool_calls: int = 0) -> None:
        """``max_tool_calls`` bounds the ``delta.tool_calls[].index`` range; ``0`` is a
        tools-disabled route, where any ``tool_calls`` delta is malformed."""
        self._buffer = bytearray()
        self._done = False
        self._malformed = False
        self._max_tool_calls = max_tool_calls
        self._first_id: dict[int, str] = {}
        self._first_name: dict[int, str] = {}

    def finished(self) -> bool:
        """``[DONE]`` was decoded and no bytes are left buffered.

        A stream that ends without ``[DONE]``, or with a partial event still
        buffered (a non-SSE body, a truncated read), is not a clean end.
        """
        return self._done and not self._buffer.strip()

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
            self._done = True
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

        Only choice ``index`` 0 is read (the gateway forces ``n = 1``, D17/D26); a choice whose
        ``index`` is not a non-bool int is malformed. Each string-valued delta key maps to a
        channel of that name (so the encoder's channel->delta-key mapping round-trips), except the
        reserved keys, which are malformed: ``finish_reason`` (a meta channel only the decoder may
        mint), any key containing ``#`` or starting ``output_tool_`` (only the decoder mints the
        per-call tool channels). ``delta.tool_calls`` becomes ``output_tool_{id,name,arguments}#n``
        deltas; ``delta.function_call`` is malformed (D1b); a choice ``finish_reason`` in the closed
        set becomes a ``("finish_reason", value)`` delta. A missing/empty ``choices`` list yields an
        empty tuple. Returns ``None`` (malformed) on any unrecognised shape.
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
            position = choice.get("index", 0)
            if isinstance(position, bool) or not isinstance(position, int):
                return None
            if position >= 1:
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                return None
            if not self._delta_channels(delta, deltas):
                return None
            finish = choice.get("finish_reason")
            if finish is not None:
                if not isinstance(finish, str) or finish not in FINISH_REASONS:
                    return None
                deltas.append((_FINISH_CHANNEL, finish))
        return tuple(deltas)

    def _delta_channels(self, delta: Mapping[str, object], out: list[tuple[str, str]]) -> bool:
        """Append one delta object's channels to ``out``; ``False`` means malformed."""
        if delta.get("function_call") is not None:
            return False
        for key, value in delta.items():
            if key == "tool_calls":
                if value is not None and not self._tool_calls(value, out):
                    return False
            elif key == "function_call":
                continue
            elif key == _FINISH_CHANNEL or _KEY_MARK in key or key.startswith(_TOOL_CHANNEL_PREFIX):
                return False
            elif isinstance(value, str):
                out.append((key, value))
        return True

    def _tool_calls(self, value: object, out: list[tuple[str, str]]) -> bool:
        if not isinstance(value, list) or self._max_tool_calls <= 0:
            return False
        for fragment in value:
            if not isinstance(fragment, dict):
                return False
            index = fragment.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                return False
            if not 0 <= index < self._max_tool_calls:
                return False
            if fragment.get("type") not in (None, "function"):
                return False
            function = fragment.get("function")
            if function is not None and not isinstance(function, dict):
                return False
            names = (fragment.get("id"), (function or {}).get("name"))
            arguments = (function or {}).get("arguments")
            if any(item is not None and not isinstance(item, str) for item in (*names, arguments)):
                return False
            if not self._first_value(self._first_id, index, names[0], _TOOL_ID, out):
                return False
            if not self._first_value(self._first_name, index, names[1], _TOOL_NAME, out):
                return False
            if arguments:
                out.append((f"{_TOOL_ARGUMENTS}{_KEY_MARK}{index}", str(arguments)))
        return True

    @staticmethod
    def _first_value(
        seen: dict[int, str], index: int, value: object, channel: str, out: list[tuple[str, str]]
    ) -> bool:
        """Keep the FIRST id / name per index; drop an equal repeat, reject a differing one."""
        if not isinstance(value, str) or not value:
            return True
        first = seen.get(index)
        if first is None:
            seen[index] = value
            out.append((f"{channel}{_KEY_MARK}{index}", value))
            return True
        return first == value
