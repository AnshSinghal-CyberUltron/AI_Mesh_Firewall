# Validates: Requirements 3.1
"""The bounded body reader stops at the bound and never assembles an oversized body."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from gateway_v2.edge.asgi_sse import read_body_bounded


def _receive(chunks: list[bytes]):  # type: ignore[no-untyped-def]
    events = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ]
    reads = {"count": 0}

    async def receive() -> Mapping[str, object]:
        reads["count"] += 1
        return events.pop(0) if events else {"type": "http.disconnect"}

    return receive, reads


def test_within_bound_is_assembled() -> None:
    receive, _ = _receive([b"ab", b"cd"])
    assert asyncio.run(read_body_bounded(receive, 4)) == b"abcd"


def test_over_bound_stops_reading() -> None:
    receive, reads = _receive([b"ab", b"cd", b"ef", b"gh"])
    assert asyncio.run(read_body_bounded(receive, 3)) is None
    assert reads["count"] == 2


def test_empty_body() -> None:
    receive, _ = _receive([b""])
    assert asyncio.run(read_body_bounded(receive, 0)) == b""
