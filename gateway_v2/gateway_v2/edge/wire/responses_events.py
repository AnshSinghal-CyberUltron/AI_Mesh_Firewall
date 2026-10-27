"""Chat SSE to Responses typed events (GW15 close-out, task 3.1).

Pure over the chat stream bytes the shipped chat route already writes. The
translator does not scan or decide. A content delta the chat pipeline released
becomes a ``response.output_text.delta``. A chat error frame ends the stream on
``response.failed``. The chat ``amf.trace`` frame is folded into the terminal
response object and is not sent as its own event.

The translator is fed incrementally and finished once. ``finish`` is separate so
the caller can persist :meth:`ResponsesEventTranslator.final_response` before
the terminal event is written.

No capacity literal and no module-level mutable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from gateway_v2.domain import posture

__all__ = (
    "COMPLETED",
    "CREATED",
    "FAILED",
    "ResponsesEventTranslator",
    "encode_event",
)

CREATED = "response.created"
ITEM_ADDED = "response.output_item.added"
PART_ADDED = "response.content_part.added"
TEXT_DELTA = "response.output_text.delta"
TEXT_DONE = "response.output_text.done"
ITEM_DONE = "response.output_item.done"
COMPLETED = "response.completed"
FAILED = "response.failed"

_TRACE_EVENT = "amf.trace"
_DONE_PAYLOAD = "[DONE]"
_CONSOLE_KEYS = ("zeroshield", "pipeline_trace")
_FRAME_SEPARATOR = b"\n\n"


def encode_event(event_type: str, payload: Mapping[str, object]) -> bytes:
    """One Responses frame: ``event: <type>`` then ``data: <json>`` then a blank line."""
    body = json.dumps(dict(payload), ensure_ascii=True, separators=(",", ":"))
    return b"event: " + event_type.encode("ascii") + b"\ndata: " + body.encode("ascii") + b"\n\n"


class ResponsesEventTranslator:
    """Turns one chat SSE stream into one Responses event stream.

    ``start`` writes the opening events. ``feed`` returns the delta events for the
    complete chat frames in ``raw`` and buffers a partial tail. ``finish`` writes
    the closing events, ending on ``response.completed`` or ``response.failed``.
    Each method returns bytes. Nothing here performs I/O.
    """

    __slots__ = (
        "_buffer",
        "_console",
        "_created_at",
        "_done",
        "_error_code",
        "_finished",
        "_item_id",
        "_model",
        "_response_id",
        "_sequence",
        "_texts",
    )

    def __init__(self, *, response_id: str, item_id: str, model: str, created_at: int) -> None:
        self._response_id = response_id
        self._item_id = item_id
        self._model = model
        self._created_at = created_at
        self._buffer = bytearray()
        self._texts: list[str] = []
        self._console: dict[str, object] = {}
        self._error_code: str | None = None
        self._done = False
        self._finished = False
        self._sequence = 0

    @property
    def error_code(self) -> str | None:
        """The chat error code that ended the stream, if any."""
        return self._error_code

    @property
    def output_text(self) -> str:
        """The text released so far."""
        return "".join(self._texts)

    def start(self) -> bytes:
        """``response.created``, ``response.output_item.added``, ``response.content_part.added``."""
        created = self._event(CREATED, {"response": self._response("in_progress", "")})
        item = self._event(
            ITEM_ADDED,
            {"output_index": 0, "item": self._message_item("in_progress", "")},
        )
        part = self._event(
            PART_ADDED,
            {
                "item_id": self._item_id,
                "output_index": 0,
                "content_index": 0,
                "part": _text_part(""),
            },
        )
        return created + item + part

    def feed(self, raw: bytes) -> bytes:
        """Delta events for every complete chat frame in ``raw``."""
        self._buffer.extend(raw)
        out = bytearray()
        while True:
            index = self._buffer.find(_FRAME_SEPARATOR)
            if index == -1:
                break
            frame = bytes(self._buffer[:index])
            del self._buffer[: index + len(_FRAME_SEPARATOR)]
            if frame:
                out.extend(self._frame(frame))
        return bytes(out)

    def final_response(self) -> dict[str, object]:
        """The object ``finish`` would put on the terminal event, given no new failure."""
        if self._failed_code() is not None:
            return self._response("failed", "")
        return self._response("completed", self.output_text)

    def finish(self, *, failure_code: str | None = None) -> bytes:
        """The closing events. ``failure_code`` fails a stream that was otherwise clean."""
        if self._finished:
            return b""
        self._finished = True
        if failure_code is not None and self._error_code is None:
            self._error_code = failure_code
        code = self._failed_code()
        if code is not None:
            return self._failed(code)
        return self._completed()

    def _failed_code(self) -> str | None:
        """The error that fails the stream. A tail with no ``[DONE]`` fails it too."""
        if self._error_code is not None:
            return self._error_code
        if self._buffer.strip() or not self._done:
            return posture.STREAM_MALFORMED_UPSTREAM
        return None

    def _frame(self, frame: bytes) -> bytes:
        """Route one complete chat frame."""
        event, data = _fields(frame)
        if data is None:
            self._error_once(posture.STREAM_MALFORMED_UPSTREAM)
            return b""
        if data == _DONE_PAYLOAD:
            self._done = True
            return b""
        try:
            obj = json.loads(data)
        except ValueError:
            self._error_once(posture.STREAM_MALFORMED_UPSTREAM)
            return b""
        if not isinstance(obj, dict):
            self._error_once(posture.STREAM_MALFORMED_UPSTREAM)
            return b""
        if event == _TRACE_EVENT:
            self._take_console(obj)
            return b""
        return self._chat_object(obj)

    def _chat_object(self, obj: Mapping[str, object]) -> bytes:
        """A chat error frame or a content chunk."""
        error = obj.get("error")
        if isinstance(error, Mapping):
            code = error.get("code")
            if not isinstance(code, str) or not code:
                code = posture.STREAM_MALFORMED_UPSTREAM
            self._error_once(code)
            return b""
        self._take_console(obj)
        if self._error_code is not None:
            return b""
        out = bytearray()
        for text in _content_texts(obj):
            self._texts.append(text)
            out.extend(
                self._event(
                    TEXT_DELTA,
                    {
                        "item_id": self._item_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": text,
                    },
                )
            )
        return bytes(out)

    def _take_console(self, obj: Mapping[str, object]) -> None:
        """Keep the last ``zeroshield`` and ``pipeline_trace`` objects, without evidence."""
        for key in _CONSOLE_KEYS:
            value = obj.get(key)
            if isinstance(value, Mapping):
                stripped = _strip_evidence(value)
                if isinstance(stripped, dict):
                    self._console[key] = stripped

    def _error_once(self, code: str) -> None:
        """The first error wins. A later error does not replace it."""
        if self._error_code is None:
            self._error_code = code

    def _completed(self) -> bytes:
        """``output_text.done``, ``output_item.done``, ``response.completed``."""
        text = self.output_text
        done = self._event(
            TEXT_DONE,
            {"item_id": self._item_id, "output_index": 0, "content_index": 0, "text": text},
        )
        item = self._event(
            ITEM_DONE,
            {"output_index": 0, "item": self._message_item("completed", text)},
        )
        completed = self._event(COMPLETED, {"response": self._response("completed", text)})
        return done + item + completed

    def _failed(self, code: str) -> bytes:
        """One ``response.failed``. The failed object's ``output_text`` is empty."""
        response = self._response("failed", "")
        response["error"] = {"code": code, "message": "The response stream did not complete."}
        return self._event(FAILED, {"response": response})

    def _response(self, status: str, text: str) -> dict[str, object]:
        """The Responses object carried by created, completed, and failed."""
        output: list[object] = []
        if status == "completed":
            output.append(self._message_item("completed", text))
        response: dict[str, object] = {
            "id": self._response_id,
            "object": "response",
            "created_at": self._created_at,
            "model": self._model,
            "status": status,
            "output": output,
            "output_text": text,
            "error": None,
        }
        if status != "in_progress":
            response.update(self._console)
        return response

    def _message_item(self, status: str, text: str) -> dict[str, object]:
        """The single assistant message item."""
        content: list[object] = [_text_part(text)] if status == "completed" else []
        return {
            "id": self._item_id,
            "type": "message",
            "status": status,
            "role": "assistant",
            "content": content,
        }

    def _event(self, event_type: str, fields: Mapping[str, object]) -> bytes:
        """Encode one event with its ``type`` and the next ``sequence_number``."""
        payload: dict[str, object] = {"type": event_type, "sequence_number": self._sequence}
        payload.update(fields)
        self._sequence += 1
        return encode_event(event_type, payload)


def _text_part(text: str) -> dict[str, object]:
    return {"type": "output_text", "text": text, "annotations": []}


def _fields(frame: bytes) -> tuple[str | None, str | None]:
    """The ``event`` name and the joined ``data`` value of one SSE frame."""
    try:
        text = frame.decode("utf-8")
    except UnicodeDecodeError:
        return None, None
    event: str | None = None
    data: list[str] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line or line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if not data:
        return event, None
    return event, "\n".join(data)


def _content_texts(obj: Mapping[str, object]) -> list[str]:
    """The ``content`` delta strings of a chat chunk, in choice order."""
    choices = obj.get("choices")
    if not isinstance(choices, list):
        return []
    texts: list[str] = []
    for choice in choices:
        if not isinstance(choice, Mapping):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, Mapping):
            continue
        content = delta.get("content")
        if isinstance(content, str) and content:
            texts.append(content)
    return texts


def _strip_evidence(value: object) -> object:
    """Drop every key named ``evidence``. Other keys are copied."""
    if isinstance(value, Mapping):
        return {
            str(key): _strip_evidence(item) for key, item in value.items() if key != "evidence"
        }
    if isinstance(value, (list, tuple)):
        return [_strip_evidence(item) for item in value]
    return value
