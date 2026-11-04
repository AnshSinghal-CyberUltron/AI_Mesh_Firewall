"""The deferred-start streaming response: a refusal before the first byte is a plain HTTP error.

OpenAI answers a streaming request that fails before it produces anything with an ordinary HTTP
error status, not ``200 text/event-stream`` plus an error frame. The SDKs apply their status-based
error classes and their retry policy only to a non-200, so the old eager SSE start meant a refused
stream never raised ``BadRequestError`` and was never retried.

:class:`StreamResponse` wraps the ASGI ``send`` and delays ``http.response.start`` until the first
frame that carries content (D21: a non-empty content, reasoning, refusal or tool-call delta).
Because the encoder puts ``delta.role`` on the first chunk it writes and a meta-only or empty
upstream frame never becomes an encoder chunk, there is nothing to hold while pending: a
role-only chunk, an empty-text delta, a ``finish_reason`` or a provider keep-alive writes no byte
and does not start the response, so the HTTP status stays undecided with no buffer to bound, and
a later upstream failure is still a plain pre-byte HTTP error. Time to first byte of a healthy
stream is unchanged (the start happens at the first released frame, not on a timer).

Three states: ``pending`` -> ``started`` | ``closed``. HTTP objects live only in ``edge``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from gateway_v2.edge.asgi_sse import ASGISend, end_sse, send_sse, start_sse

__all__ = ("StreamResponse",)

_PENDING = "pending"
_STARTED = "started"
_CLOSED = "closed"


class StreamResponse:
    """Per-request, mutable (like ``FirstByteLatch``); the module holds no mutable state."""

    __slots__ = ("_emit_error", "_send", "_state")

    def __init__(
        self, send: ASGISend, *, emit_error: Callable[[str], Awaitable[None]]
    ) -> None:
        """``emit_error(code)`` writes the plain HTTP error for ``code`` (the same bytes a
        non-stream request gets, with the console projection headers)."""
        self._send = send
        self._emit_error = emit_error
        self._state = _PENDING

    @property
    def send(self) -> ASGISend:
        """The underlying ASGI ``send`` (only the legacy terminal helpers need it)."""
        return self._send

    @property
    def pending(self) -> bool:
        """No byte has been sent: the status line is still undecided."""
        return self._state == _PENDING

    @property
    def started(self) -> bool:
        return self._state == _STARTED

    async def start(self) -> None:
        """Send ``http.response.start`` (200 ``text/event-stream``); idempotent."""
        if self._state == _PENDING:
            await start_sse(self._send)
            self._state = _STARTED

    async def write(self, frame: bytes) -> None:
        """Write one content-bearing frame, starting the response first when still pending."""
        if self._state == _CLOSED:
            return
        await self.start()
        await send_sse(self._send, frame)

    async def write_started(self, frame: bytes) -> None:
        """Write a frame that does not itself carry content (finish chunk, trace, ``[DONE]``)."""
        if self._state == _STARTED:
            await send_sse(self._send, frame)

    async def fail_pre_byte(self, code: str) -> None:
        """Answer ``code`` as a plain HTTP error; allowed only while pending."""
        if self._state != _PENDING:
            return
        self._state = _CLOSED
        await self._emit_error(code)

    async def close(self) -> None:
        """End the SSE body (terminal marker already written); idempotent."""
        if self._state == _STARTED:
            self._state = _CLOSED
            await end_sse(self._send)
