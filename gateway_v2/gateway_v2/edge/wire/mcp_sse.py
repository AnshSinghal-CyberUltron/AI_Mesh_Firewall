"""SSE framing for MCP replies (GW32 Requirement 4).

Deliberate deviation from R4.3 / P12 wording (design: "SSE framing"): the shipped `SSEEncoder` and
`SSEDecoder` are chat-delta shaped (`DownstreamFrame`, `[DONE]`) and reject a JSON-RPC body, so
they cannot be reused. This module reuses their framing rules (ASCII-escaped compact JSON, the
`data:` grammar) and a cross-check test ties the two decoders' `data:` grammar together.
"""

from __future__ import annotations

import json

__all__ = ("KEEPALIVE", "SSE_HEADERS", "decode_events", "encode_message")

KEEPALIVE = b": keepalive\n\n"
SSE_HEADERS: tuple[tuple[str, str], ...] = (
    ("content-type", "text/event-stream"),
    ("cache-control", "no-store"),
    ("x-accel-buffering", "no"),
)


def encode_message(body: bytes) -> bytes:
    """One ``message`` event. The JSON is re-serialised compact and ASCII-escaped, so no raw
    newline or non-ASCII byte can exist inside the event; a stray ``\\r`` or ``\\n`` that
    survived would still be split into multiple ``data:`` lines per the SSE grammar."""
    text = json.dumps(json.loads(body), ensure_ascii=True, separators=(",", ":"))
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    data = b"".join(b"data: " + line.encode("ascii") + b"\n" for line in lines)
    return b"event: message\n" + data + b"\n"


def decode_events(raw: bytes) -> list[tuple[str, str]]:
    """Test-side decoder with the `data:` field grammar of the shipped `SSEDecoder`: returns
    ``(event, data)`` pairs; comment lines (``:``) are ignored; multiple ``data:`` lines join
    with ``\\n``."""
    events: list[tuple[str, str]] = []
    for block in raw.decode("utf-8").split("\n\n"):
        event, data = "message", []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
        if data:
            events.append((event, "\n".join(data)))
    return events
