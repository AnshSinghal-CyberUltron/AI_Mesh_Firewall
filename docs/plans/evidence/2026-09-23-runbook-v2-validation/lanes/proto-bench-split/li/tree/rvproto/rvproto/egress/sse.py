"""Incremental SSE codec (upstream parse, downstream frames)."""

from __future__ import annotations

import orjson

DONE = b"[DONE]"
DONE_FRAME = b"data: [DONE]\n\n"


class SseParser:
    """Feed raw bytes; get the data payload of each COMPLETE event (partial events wait)."""

    __slots__ = ("buf",)

    def __init__(self) -> None:
        self.buf = b""

    def feed(self, data: bytes) -> list[bytes]:
        buf = self.buf + data if self.buf else data
        if b"\r" in buf:
            buf = buf.replace(b"\r\n", b"\n")
        out: list[bytes] = []
        start = 0
        while True:
            end = buf.find(b"\n\n", start)
            if end < 0:
                break
            event = buf[start:end]
            start = end + 2
            lines = [ln[5:] for ln in event.split(b"\n") if ln.startswith(b"data:")]
            if lines:
                payload = b"\n".join(ln[1:] if ln.startswith(b" ") else ln for ln in lines)
                out.append(payload)
        self.buf = buf[start:]
        return out


def frame(obj: object) -> bytes:
    return b"data: " + orjson.dumps(obj) + b"\n\n"


def error_frame(message: str, code: str) -> bytes:
    return frame({"error": {"message": message, "type": "stream_terminated", "param": None,
                            "code": code}})
