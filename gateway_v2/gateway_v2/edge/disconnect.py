"""Client-disconnect cancellation for a streaming chat response (GW34 R6, D14, D22).

``ChatRoute.handle`` never read ``receive`` after the request body, and uvicorn discards ``send``
after a disconnect, so an abandoned stream kept running to completion, holding its admission slot,
its provider connection and its spend. :func:`run_until_disconnect` runs the stream body as an
INNER ``asyncio.Task`` and starts a watcher that cancels only that inner task when ASGI reports
``http.disconnect`` before the stream completed.

Why the inner task and not the handler task: uvicorn's and ``httpx.ASGITransport``'s ``receive()``
return ``http.disconnect`` once the response is COMPLETE, not only when the client leaves, so the
watcher must never cancel the handler (its ``finally`` releases the holds and writes the audit
record exactly once). :class:`StreamingDone` is set before the terminal write, so a disconnect
reported after the stream finished is a no-op.

Telling a watcher cancel from a server cancel (D22, Python 3.12): both surface as
``CancelledError`` at the same ``await``. The watcher cancelled the INNER task, so
``inner.cancelled() and current_task().cancelling() == 0`` means "the client left": the handler
continues normally (nothing is written to the departed client, so no error frame and no second
terminal frame). Otherwise the server cancelled the handler: the inner task is cancelled and
awaited (never leaked) and the cancellation is re-raised.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from gateway_v2.edge.asgi_sse import ASGIReceive

__all__ = ("StreamingDone", "run_until_disconnect", "watch_disconnect")

_DISCONNECT = "http.disconnect"


class StreamingDone:
    """A one-way per-request flag: the stream reached its terminal write (slotted)."""

    __slots__ = ("_set",)

    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set


async def watch_disconnect(
    receive: ASGIReceive, stream_task: asyncio.Future[None], done: StreamingDone
) -> None:
    """Await ``http.disconnect``; cancel ``stream_task`` unless the stream already finished."""
    while True:
        message = await receive()
        if message.get("type") == _DISCONNECT:
            if not done.is_set():
                stream_task.cancel()
            return


async def run_until_disconnect(
    receive: ASGIReceive, make_stream: Callable[[StreamingDone], Awaitable[None]]
) -> bool:
    """Run the stream body under the watcher; ``True`` when the client left before it finished."""
    done = StreamingDone()
    inner: asyncio.Future[None] = asyncio.ensure_future(make_stream(done))
    watcher = asyncio.ensure_future(watch_disconnect(receive, inner, done))
    try:
        await inner
        return False
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if inner.cancelled() and current is not None and current.cancelling() == 0:
            return True
        inner.cancel()
        await asyncio.wait({inner})
        raise
    finally:
        watcher.cancel()
        await asyncio.wait({watcher})
