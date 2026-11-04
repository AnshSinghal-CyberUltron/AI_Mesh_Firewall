"""A conforming ASGI ``receive`` for in-process harnesses that reach a STREAMING chat route.

uvicorn and ``httpx.ASGITransport`` return ``http.disconnect`` only once the response is COMPLETE
(or the client left). A harness ``receive`` stub that answers ``http.disconnect`` as soon as the
request body is consumed models a client that leaves before the first frame, which the GW34
disconnect watcher (rightly) treats as a real early cancel. Streaming harnesses use
:func:`receive_until_sent` instead: it delivers the body events, then blocks until the harness's
``send`` has seen the final body frame, then reports the disconnect.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence

__all__ = ["mark_sent", "receive_until_sent"]


def receive_until_sent(
    body_events: Sequence[Mapping[str, object]], sent: asyncio.Event
) -> Callable[[], Awaitable[Mapping[str, object]]]:
    """``receive`` yielding ``body_events``, then ``http.disconnect`` only after ``sent`` is set."""
    pending = list(body_events)

    async def receive() -> Mapping[str, object]:
        if pending:
            return pending.pop(0)
        await sent.wait()
        return {"type": "http.disconnect"}

    return receive


def mark_sent(sent: asyncio.Event, message: Mapping[str, object]) -> None:
    """Call from the harness ``send``: set ``sent`` on the final body frame of the response."""
    if message.get("type") == "http.response.body" and not message.get("more_body", False):
        sent.set()
