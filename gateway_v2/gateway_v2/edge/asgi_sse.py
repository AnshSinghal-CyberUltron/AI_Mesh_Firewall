"""ASGI/SSE wire plumbing for the chat route (GW15 — split from ``edge/routes.py``).

``edge/routes.py`` owns the route table + :class:`~gateway_v2.edge.routes.ChatRoute` (the §10.4
lifecycle dispatch + the shipped egress orchestration); this sibling module holds the small,
leaf-level ASGI/SSE wire helpers that route depends on — the minimal ASGI 3.0 type aliases, the
request-body reader, the SSE framing writers, the one-way terminal latch, and the provider→
``UpstreamChunk`` adapter with its mid-stream error-frame scan glue. Pulling them here keeps
``routes.py`` under the 800-line module gate while leaving behaviour byte-for-byte unchanged: the
helpers are the same functions, moved, and ``routes.py`` imports them back.

Everything here is ``edge`` (the top layer), so it imports every layer below; the scanner, the
resolver, and the ``ProviderClient`` are injected (via :class:`_ErrorFrameScan` / the adapter
arguments) from ``routes.py`` above ``detect`` — ``egress`` never imports ``detect`` (R14.1), and
nothing here reaches around the layer contract. No module-level mutable; the SSE header tuple and
the ASGI event-key constants are frozen named constants, not capacity-position literals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass

from gateway_v2.dispatch.provider import UpstreamEvent
from gateway_v2.edge.cancel import InFlightControl, KillLatch
from gateway_v2.edge.error_frame_scan import ErrorFrameScanner, scan_error_frame_offloaded
from gateway_v2.edge.executor import ScanExecutor
from gateway_v2.edge.stream_timeouts import StreamTimeouts
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.egress.output_guard import OutputResolver
from gateway_v2.egress.stream import UpstreamChunk

__all__ = (
    "ASGIReceive",
    "ASGIScope",
    "ASGISend",
)

# --------------------------------------------------------------------------- #
# Minimal ASGI 3.0 type aliases (the repo pulls in no framework).
# --------------------------------------------------------------------------- #

#: An ASGI connection scope — the ``{"type": ..., "method": ..., "path": ...}`` mapping the
#: server hands the application. Typed as a read-only ``Mapping`` because the application never
#: mutates it.
ASGIScope = Mapping[str, object]
#: The ASGI ``receive`` awaitable: pulls the next event (request body chunk, ``http.disconnect``)
#: off the connection. Each event is a plain ``{"type": ...}`` mapping.
ASGIReceive = Callable[[], Awaitable[Mapping[str, object]]]
#: The ASGI ``send`` awaitable: pushes a response event (``http.response.start`` /
#: ``http.response.body``) onto the connection.
ASGISend = Callable[[Mapping[str, object]], Awaitable[None]]


# --------------------------------------------------------------------------- #
# Small internal helpers (SSE framing on the ASGI channel; adapters).
# --------------------------------------------------------------------------- #

#: SSE content type with the ``no-cache`` + ``keep-alive`` headers a streaming client expects.
_SSE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-type", b"text/event-stream"),
    (b"cache-control", b"no-cache"),
    (b"connection", b"keep-alive"),
)

#: The ASGI ``http.request`` event keys the body reader consumes: each event carries a ``body``
#: byte chunk and a ``more_body`` flag that is true while further chunks follow. Named constants so
#: the one reader keeps a single spelling; not capacity-position literals.
_ASGI_EVENT_TYPE_KEY = "type"
_ASGI_HTTP_REQUEST = "http.request"
_ASGI_BODY_KEY = "body"
_ASGI_MORE_BODY_KEY = "more_body"


async def read_body(receive: ASGIReceive) -> bytes:
    """Assemble the full request body from the ASGI ``receive`` channel.

    Drains ``http.request`` events, concatenating each event's ``body`` chunk while ``more_body``
    is true, and stops on the first event that clears ``more_body`` (or on any non-request event,
    e.g. ``http.disconnect`` — a client that drops mid-body contributes nothing further). The
    assembled bytes are what the §10.4 lifecycle parses; the Pre_Identity_Clock size bound (read
    from the injected ``ResourceContract`` inside the lifecycle, no literal here) runs on these
    assembled bytes, so an oversized body is refused there before canonicalization — this reader's
    job is purely to turn the chunked channel into one ``bytes`` value, correctly handling
    ``more_body``.
    """
    chunks: list[bytes] = []
    while True:
        event = await receive()
        if event.get(_ASGI_EVENT_TYPE_KEY) != _ASGI_HTTP_REQUEST:
            break
        chunk = event.get(_ASGI_BODY_KEY)
        if isinstance(chunk, (bytes, bytearray)):
            chunks.append(bytes(chunk))
        if not event.get(_ASGI_MORE_BODY_KEY, False):
            break
    return b"".join(chunks)


async def read_body_bounded(receive: ASGIReceive, max_bytes: int) -> bytes | None:
    """Read the body like :func:`read_body`, but stop past ``max_bytes`` and return ``None``.

    The bound is checked as each chunk arrives, so an oversized body is never assembled.
    ``max_bytes`` comes from the caller's ``ResourceContract``; no bound lives here.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        event = await receive()
        if event.get(_ASGI_EVENT_TYPE_KEY) != _ASGI_HTTP_REQUEST:
            break
        chunk = event.get(_ASGI_BODY_KEY)
        if isinstance(chunk, (bytes, bytearray)):
            total += len(chunk)
            if total > max_bytes:
                return None
            chunks.append(bytes(chunk))
        if not event.get(_ASGI_MORE_BODY_KEY, False):
            break
    return b"".join(chunks)


async def start_sse(send: ASGISend) -> None:
    """Open a ``200`` SSE response on the ASGI channel (``more_body`` keeps it streaming)."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": list(_SSE_HEADERS),
        }
    )


async def send_sse(send: ASGISend, frame: bytes) -> None:
    """Write one SSE frame as a non-terminal ``http.response.body`` event (``more_body`` set)."""
    await send({"type": "http.response.body", "body": frame, "more_body": True})


async def end_sse(send: ASGISend) -> None:
    """Close the SSE response with an empty terminal body event (``more_body`` cleared)."""
    await send({"type": "http.response.body", "body": b"", "more_body": False})


class TerminalLatch:
    """A one-way latch so a terminal SSE frame is emitted at most once per stream."""

    __slots__ = ("_set",)

    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set


@dataclass(frozen=True, slots=True)
class ErrorFrameScan:
    """The injected glue the chunk adapter needs to scan a mid-stream error frame (R11).

    Bundles the three pieces :func:`as_chunks` threads into the error-frame scan step so the
    adapter's signature stays small: the findings ``scanner`` and the ``resolver`` (both injected
    into :class:`~gateway_v2.edge.routes.ChatRoute` above ``detect``, so ``egress`` never imports
    ``detect`` — R14.1) and the per-request :class:`KillLatch` a WITHHELD frame flips with its
    terminal posture code. The handler's :meth:`~gateway_v2.edge.routes.ChatRoute._finish_stream`
    then renders that code as the declared ``Error_Frame`` at the single terminal site — the same
    seam a timeout / in-flight cut uses. Frozen + slotted: a per-attempt value, not mutable state.
    """

    scanner: ErrorFrameScanner
    resolver: OutputResolver
    kill_latch: KillLatch
    executor: ScanExecutor


async def forward_error_frame(frame: str, error_scan: ErrorFrameScan) -> str | None:
    """Scan a mid-stream error frame; return the bytes to forward, or ``None`` to withhold (R11).

    Runs the single whole-text scan + decision OFF the serving loop via
    :func:`~gateway_v2.edge.error_frame_scan.scan_error_frame_offloaded` (C38 / R13.1): the
    CPU-bound scan is handed to the contract-sized GIL-releasing executor so one stream's error
    frame cannot starve another stream's latency, and the executor records the serving-loop lag as
    the R13.4 SLO input. The scan itself is byte-linear and fails closed exactly as before (R11.5 /
    R11.4). A REDACT/ALLOW outcome returns the frame text with the decided redactions applied — the
    only bytes of the frame that ever leave the gateway (R11.2). A WITHHELD outcome (BLOCK →
    ``output_blocked``, R11.3; scan error → ``scan_failure``, R11.4, fail closed) flips the shared
    :class:`KillLatch` with the terminal posture code and returns ``None``, so the caller stops the
    source and the handler renders the declared ``Error_Frame`` from the latch reason. No raw
    error-frame byte is forwarded on a withhold (R15.1/R15.2).
    """
    outcome = await scan_error_frame_offloaded(
        frame,
        scanner=error_scan.scanner,
        resolver=error_scan.resolver,
        executor=error_scan.executor,
    )
    if outcome.withheld_code is not None:
        error_scan.kill_latch.kill(outcome.withheld_code)
        return None
    return outcome.forward_text


async def as_chunks(
    events: AsyncIterator[UpstreamEvent],
    coalescer: Coalescer,
    control: InFlightControl,
    timeouts: StreamTimeouts,
    *,
    error_scan: ErrorFrameScan,
) -> AsyncIterator[UpstreamChunk]:
    """Adapt a provider event stream to the shipped ``UpstreamChunk`` source, bounded + cut-aware.

    Each ``UpstreamEvent`` becomes an ``UpstreamChunk`` the pipeline consumes; payload bytes are
    offered to the bounded ``Coalescer`` (``offer`` False = backpressure at the derived
    high-water) and released as handed on (GW9/GW13 wire the credit-paced consumer).

    **In-flight control (R10.2-R10.7).** Each yielded chunk is a boundary where
    :class:`InFlightControl` is evaluated BEFORE the next chunk: a fired trigger (max duration,
    kill switch, key revocation, plan change, stale/unavailable snapshot — fail closed) flips the
    shared :class:`KillLatch` and stops the source, so no byte is forwarded after the cut, within
    the derived ``max_cut_latency_s()`` (``InFlightKill.CUT_NEXT_CHUNK``). The handler renders the
    latch reason as the terminal ``Error_Frame`` (``ChatRoute._finish_stream``).

    **Inter-chunk timeout (R9.2/R9.5).** The next-event ``await`` is bounded by
    :meth:`StreamTimeouts.next_event` (derived ``inter_chunk_timeout_s()``, no literal); a breach
    flips the latch with ``STREAM_INTER_CHUNK_TIMEOUT`` and raises :class:`StreamTimeout` so the
    stream unwinds. ``__anext__`` is driven explicitly so the per-event await is the bounded one;
    ``StopAsyncIteration`` ends the stream normally.

    **Mid-stream error frame (R11.1-R11.5).** An ``error_frame`` is an untrusted free-text body
    that may hold a secret, so BEFORE any byte is forwarded it is scanned off-loop
    (``scan_error_frame`` via the injected scanner + resolver). REDACT/ALLOW forwards the
    (masked) frame as a FINAL content chunk (R11.2 — the only bytes that leave); BLOCK (R11.3) or
    a scan error (R11.4, fail closed) withholds it, flips the latch (``output_blocked`` /
    ``scan_failure``), and stops the source. The scan is one byte-linear pass (R11.5); an error
    frame is terminal either way.
    """
    # Evaluate before the first chunk too: a max-duration/stale/kill signal already true at stream
    # start must cut before any upstream byte is forwarded (R10.7, fail-closed).
    if control.evaluate() is not None:
        return
    iterator = events.__aiter__()
    while True:
        # Bound the wait for the NEXT upstream event by the Inter_Chunk_Timeout (R9.2). Driving
        # __anext__ explicitly (rather than `async for`) is what lets the per-event await be
        # wrapped; a breach raises StreamTimeout (latch already flipped) and unwinds the stream.
        try:
            event = await timeouts.next_event(iterator.__anext__())
        except StopAsyncIteration:
            return

        # Mid-stream provider error frame (R11): scan it BEFORE forwarding any byte. A REDACT/ALLOW
        # decision forwards the (possibly masked) frame as a FINAL content chunk and ends the
        # stream (R11.2); a BLOCK (R11.3) or a scan error (R11.4) withholds the frame entirely —
        # the kill latch is flipped with the terminal code and the source stops, so no byte of the
        # frame is forwarded and the handler renders the declared Error_Frame from the latch.
        if event.error_frame is not None:
            forwarded = await forward_error_frame(event.error_frame, error_scan)
            if forwarded is None:
                return  # withheld: latch carries the terminal code (R11.3 / R11.4)
            nbytes = len(forwarded.encode("utf-8"))
            coalescer.offer(nbytes)
            yield UpstreamChunk(text_deltas=(("content", forwarded),), final=True)
            coalescer.release(nbytes)
            return

        text = "".join(delta for _, delta in event.text_deltas)
        nbytes = len(text.encode("utf-8"))
        coalescer.offer(nbytes)
        yield UpstreamChunk(text_deltas=event.text_deltas, final=event.final)
        coalescer.release(nbytes)
        if event.final:
            return
        # Chunk boundary: poll every R10 trigger. A fired trigger stops the source so no further
        # upstream bytes reach the caller after the cut (R10.7).
        if control.evaluate() is not None:
            return
