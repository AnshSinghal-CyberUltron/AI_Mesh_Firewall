"""A loopback OpenAI-format upstream for the ``wire`` matrix mode (D8).

Runs under uvicorn on ``127.0.0.1:0`` and emits scripted SSE bytes, splitting tool-call fragments
at seeded random byte offsets (mid-JSON-string, mid-escape, mid-UTF-8, mid-SSE-line) so the real
``OpenAIUpstreamClient`` / ``SSEDecoder`` / ``ToolCallAssembler`` are exercised end to end over a
real socket. Test tooling only: never imported by the shipped gateway.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence

__all__ = ("chat_completion_chunk", "fake_upstream_app", "split_sse_bytes")


def chat_completion_chunk(delta: dict[str, object], finish_reason: str | None = None) -> bytes:
    body = {
        "id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 0, "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return b"data: " + json.dumps(body).encode() + b"\n\n"


def split_sse_bytes(wire: bytes, rng: random.Random, *, pieces: int) -> list[bytes]:
    """Split ``wire`` at ``pieces`` seeded random byte offsets (anywhere, including mid-escape)."""
    if pieces <= 1 or len(wire) < 2:
        return [wire]
    cuts = sorted(rng.randint(1, len(wire) - 1) for _ in range(pieces - 1))
    bounds = [0, *cuts, len(wire)]
    return [wire[a:b] for a, b in zip(bounds, bounds[1:], strict=False) if wire[a:b]]


def fake_upstream_app(scripts: dict[str, Sequence[bytes]], *, default: str) -> object:
    """A raw-ASGI app: ``POST /v1/chat/completions`` streams the script named by the request
    marker (see :mod:`harness`); anything else is a 404. Imports ``uvicorn``/``starlette``-free
    raw ASGI, matching the shipped gateway's own style, so this stays a thin test double."""

    async def app(scope: dict[str, object], receive: object, send: object) -> None:
        if scope.get("type") == "lifespan":
            while True:
                message = await receive()  # type: ignore[operator]
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})  # type: ignore[operator]
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})  # type: ignore[operator]
                    return
            return
        body = b""
        more = True
        while more:
            message = await receive()  # type: ignore[operator]
            body += message.get("body", b"")
            more = message.get("more_body", False)
        name = default
        try:
            parsed = json.loads(body)
            for message_obj in reversed(parsed.get("messages") or ()):
                content = message_obj.get("content")
                if isinstance(content, str) and content.startswith("SDKMATRIX:"):
                    name = content[len("SDKMATRIX:"):]
                    break
        except (ValueError, AttributeError):
            pass
        frames = scripts.get(name, scripts[default])
        await send(  # type: ignore[operator]
            {
                "type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        for frame in frames:
            await send({"type": "http.response.body", "body": frame, "more_body": True})  # type: ignore[operator]
        await send({"type": "http.response.body", "body": b"", "more_body": False})  # type: ignore[operator]

    return app
