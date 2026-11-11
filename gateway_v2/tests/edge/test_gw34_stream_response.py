"""GW34 Task 9: the StreamResponse state machine (pending / started / closed) over a fake send."""
from __future__ import annotations

import asyncio
from typing import Any

from gateway_v2.edge.stream_response import StreamResponse


class _Send:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


def _make() -> tuple[StreamResponse, _Send, list[str]]:
    send = _Send()
    errors: list[str] = []

    async def emit_error(code: str) -> None:
        errors.append(code)

    return StreamResponse(send, emit_error=emit_error), send, errors  # type: ignore[arg-type]


def test_pending_until_the_first_content_write_which_starts_the_response() -> None:
    response, send, _ = _make()
    assert response.pending and send.messages == []
    asyncio.run(response.write(b"data: x\n\n"))
    assert response.started
    assert [m["type"] for m in send.messages] == ["http.response.start", "http.response.body"]
    assert send.messages[0]["status"] == 200
    assert (b"content-type", b"text/event-stream") in send.messages[0]["headers"]


def test_a_pre_byte_failure_emits_the_plain_error_and_closes_without_starting_sse() -> None:
    response, send, errors = _make()
    asyncio.run(response.fail_pre_byte("upstream_timeout"))
    assert errors == ["upstream_timeout"] and send.messages == []
    asyncio.run(response.write(b"data: late\n\n"))  # nothing after a plain error
    assert send.messages == []


def test_fail_pre_byte_is_refused_once_started() -> None:
    response, send, errors = _make()
    asyncio.run(response.write(b"data: x\n\n"))
    asyncio.run(response.fail_pre_byte("upstream_timeout"))
    assert errors == [] and response.started


def test_non_content_frames_are_dropped_while_pending_and_written_once_started() -> None:
    response, send, _ = _make()
    asyncio.run(response.write_started(b"data: finish\n\n"))
    assert send.messages == []
    asyncio.run(response.start())
    asyncio.run(response.write_started(b"data: [DONE]\n\n"))
    assert send.messages[-1]["body"] == b"data: [DONE]\n\n"


def test_close_ends_the_body_once() -> None:
    response, send, _ = _make()
    asyncio.run(response.write(b"data: x\n\n"))
    asyncio.run(response.close())
    asyncio.run(response.close())
    ends = [
        m for m in send.messages if m["type"] == "http.response.body" and not m.get("more_body")
    ]
    assert len(ends) == 1
