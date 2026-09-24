"""Pure-ASGI helpers (no framework middleware on the hot path)."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
Headers = Sequence[tuple[bytes, bytes]]

_RID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
JSON_CT = (b"content-type", b"application/json")


class BodyTooLarge(Exception):
    pass


async def read_body(receive: Receive, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        msg = await receive()
        if msg["type"] == "http.disconnect":
            raise asyncio.CancelledError("client disconnected before body completed")
        body = msg.get("body", b"")
        size += len(body)
        if size > limit:
            raise BodyTooLarge()
        chunks.append(body)
        if not msg.get("more_body", False):
            break
    return chunks[0] if len(chunks) == 1 else b"".join(chunks)


async def wait_disconnect(receive: Receive) -> None:
    while True:
        msg = await receive()
        if msg["type"] == "http.disconnect":
            return


def header_map(scope: Scope) -> dict[bytes, bytes]:
    return {k.lower(): v for k, v in scope["headers"]}


def request_id(h: dict[bytes, bytes]) -> str:
    raw = h.get(b"x-request-id")
    if raw:
        s = raw.decode("latin-1")
        if _RID.match(s):
            return s
    return uuid.uuid4().hex


def bearer(h: dict[bytes, bytes]) -> str | None:
    raw = h.get(b"authorization")
    if not raw:
        return None
    s = raw.decode("latin-1").strip()
    if s[:7].lower() == "bearer ":
        return s[7:].strip() or None
    return None


async def send_bytes(send: Send, status: int, body: bytes, headers: Headers,
                     content_type: tuple[bytes, bytes] = JSON_CT) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [content_type, (b"content-length", str(len(body)).encode()), *headers]})
    await send({"type": "http.response.body", "body": body, "more_body": False})
