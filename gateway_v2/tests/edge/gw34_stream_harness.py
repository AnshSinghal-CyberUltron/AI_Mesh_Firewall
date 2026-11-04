"""Shared harness for the GW34 streaming tests: a dev-profile app and a recording ASGI call."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from tests.edge.test_gw36_s1_upstream_body import KEY, _app, _plain


@dataclass(frozen=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes
    messages: list[dict[str, Any]]

    @property
    def is_sse(self) -> bool:
        return self.headers.get("content-type", "").startswith("text/event-stream")

    def frames(self) -> list[dict[str, Any]]:
        """The decoded ``data:`` JSON frames of an SSE body (excludes ``[DONE]``)."""
        out = []
        for block in self.body.split(b"\n\n"):
            if block.startswith(b"data: ") and block != b"data: [DONE]":
                out.append(json.loads(block[len(b"data: "):]))
        return out

    def deltas(self) -> list[dict[str, Any]]:
        return [f["choices"][0]["delta"] for f in self.frames() if f.get("choices")]


class Scripted:
    """A provider replying with scripted ``UpstreamEvent``s (records the rendered request)."""

    def __init__(self, events: Sequence[UpstreamEvent]) -> None:
        self.events = list(events)
        self.sent: list[Any] = []
        self.opened = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.opened += 1
        self.sent.append(_plain(request.body["openai_body"]))  # type: ignore[arg-type]
        return self._stream()

    async def _stream(self) -> AsyncIterator[UpstreamEvent]:
        for event in self.events:
            yield event

    async def abort(self) -> None:
        return None


def content(text: str, *, final: bool = False) -> UpstreamEvent:
    return UpstreamEvent(text_deltas=(("content", text),), final=final)


def deltas(*pairs: tuple[str, str], final: bool = False) -> UpstreamEvent:
    return UpstreamEvent(text_deltas=tuple(pairs), final=final)


def failure(code: str) -> UpstreamEvent:
    return UpstreamEvent(text_deltas=(), final=True, gateway_error=code)


def call(provider: object, payload: dict[str, Any], path: str = "/v1/chat/completions") -> Reply:
    return asyncio.run(_call(_app(provider), path, {"model": "echo-1", **payload}))  # type: ignore[arg-type]


async def _call(app: Any, path: str, payload: dict[str, Any]) -> Reply:
    scope = {"type": "http", "method": "POST", "path": path,
             "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")]}
    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return Reply(int(start["status"]), headers, body, sent)
