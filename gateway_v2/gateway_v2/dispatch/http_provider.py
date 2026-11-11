"""Real streaming HTTP/SSE generation-egress ``ProviderClient`` (B4 / GW17-18).

``HttpProviderClient`` is the real :class:`~gateway_v2.dispatch.provider.ProviderClient`
that opens a streaming HTTP/SSE request to the organization-selected
``Generation_Provider`` and streams its completion back as
:class:`~gateway_v2.dispatch.provider.UpstreamEvent` values. It drops in BEHIND
the shipped ``@runtime_checkable`` ``ProviderClient`` Protocol WITHOUT changing
the Protocol or the ``UpstreamRequest`` / ``UpstreamEvent`` value types (R1.1);
``StubProviderClient`` stays the honest-boot default and a deployment injects
this client explicitly through the shipped ``build_app(provider=...)`` seam.

**Reuse, never re-shape.** The request body is produced by the shipped
``Transform`` codec: ``open`` unpacks ``UpstreamRequest.body`` under the
:data:`~gateway_v2.dispatch.transform.BODY_MODEL_KEY` /
:data:`~gateway_v2.dispatch.transform.BODY_DELTAS_KEY` constants (not hand-shaped
JSON — R1.5, R2.3), and the upstream SSE byte stream is parsed by the shipped
``edge/wire/sse.py SSEDecoder``, which buffers split UTF-8 / split JSON surrogate
pairs and sets a one-way ``malformed()`` latch on an undecodable event (R2.3).

**Layering (import-linter ``layers`` contract).** ``dispatch`` sits ABOVE
``egress`` / ``runtime`` / ``contracts`` / ``domain`` and BELOW ``edge`` /
``plan``, so this module may import ``egress`` (``DownstreamFrame``) and its
sibling ``dispatch.provider`` / ``dispatch.transform`` — but it may NOT import
``edge``. The ``SSEDecoder`` is therefore INJECTED as ``decoder_factory`` (edge
wires the real ``edge/wire/sse.py SSEDecoder`` through ``build_app`` at task
6.1), described here only by the structural :class:`SSEDecoderLike` protocol —
the same inject-never-import-upward discipline the shipped ``StreamPipeline``
uses for the detector. No new upward dispatch edge, no ``egress -> detect`` edge.

**``httpx`` is lazy / optional.** ``httpx`` is declared under the existing
``conformance`` extra (which already carries ``httpx>=0.27``) and is imported
LAZILY inside :meth:`HttpProviderClient.open` — never at module load — with a
``pip install 'gateway-v2[conformance]'`` hint on :class:`ImportError`, exactly
the pattern ``edge/serve.py`` uses for uvicorn under ``[serve]``. So
``import gateway_v2.dispatch.http_provider`` on a tree WITHOUT the extra still
succeeds; only constructing-and-opening a stream needs it. ``httpx`` types used
in annotations are imported only under ``TYPE_CHECKING`` so ``mypy --strict``
types the client without a hard runtime import.

**Accounting boundary (R1.6 / R8.1 / R8.4).** The ``Generation_Provider`` is the
organization-selected / BYOK OpenAI-compatible LLM; it is treated as
organization-owned and is EXCLUDED from firewall-infrastructure cost and latency
accounting (runbook "Customer model" constraint). No firewall cost/latency metric
observes the time spent in this client's upstream read.

**Deferred live gate (R8.1).** The REAL BYOK provider socket is the B4 live half
(GW17-18): the local half drives this client against a ``Fake_Upstream`` over a
real local socket. Nothing cloud-side is scaffolded here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Protocol

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY, BODY_MODEL_KEY
from gateway_v2.domain import posture
from gateway_v2.egress.stream import DownstreamFrame

if TYPE_CHECKING:
    # ``httpx`` is an OPTIONAL ``conformance``-extra dependency and is not
    # imported at module load (the lazy import lives in :meth:`open`). Importing
    # it only under ``TYPE_CHECKING`` lets ``mypy --strict`` type the streaming
    # client / response without a hard runtime import, so this module imports on
    # a tree WITHOUT the extra. The matching RUNTIME import is lazy — performed
    # INSIDE :meth:`open` with a ``pip install 'gateway-v2[conformance]'`` hint.
    import httpx

#: The posture code the client surfaces as a terminal ``error_frame`` when the
#: injected decoder's ``malformed()`` latch trips (an undecodable upstream SSE
#: event). Reused from ``domain.posture`` — the SINGLE spelling the SSE codec
#: also renders — not re-spelled here, so the two sides cannot drift. It is a
#: wire-shape string, not a capacity bound.
STREAM_MALFORMED_UPSTREAM = posture.STREAM_MALFORMED_UPSTREAM

__all__ = (
    "STREAM_MALFORMED_UPSTREAM",
    "ClosableResponse",
    "HttpProviderClient",
    "SSEDecoderLike",
)


class ClosableResponse(Protocol):
    """The structural shape :meth:`HttpProviderClient.abort` needs of the stream.

    The in-flight handle :meth:`HttpProviderClient._stream_events` records in
    ``self._stream`` is the httpx streaming response (the object bound by
    ``async with client.stream(...) as response``). ``abort`` needs only to
    CLOSE it — ``await response.aclose()`` stops ``aiter_bytes`` from yielding
    further bytes and releases the underlying connection back to the httpx pool
    — so the handle is narrowed to this one-method structural protocol rather
    than carrying the full ``httpx`` type at runtime (``httpx`` is a lazy /
    optional ``conformance``-extra import). The real ``httpx.Response.aclose``
    satisfies it, and ``aclose`` is safe to call more than once, so a close that
    races the ``_stream_events`` ``finally`` cannot double-crash.
    """

    async def aclose(self) -> None: ...


class SSEDecoderLike(Protocol):
    """The structural shape of the INJECTED SSE decoder the client reads.

    Mirrors the shipped ``edge/wire/sse.py SSEDecoder`` field-for-field WITHOUT
    importing ``edge`` (the layer contract forbids a ``dispatch -> edge`` edge):
    :meth:`feed` accumulates raw bytes and returns every COMPLETE
    ``DownstreamFrame`` decoded this call (a partial trailing event stays
    buffered, so a chunk boundary never splits a decoded code point), and
    :meth:`malformed` is the one-way latch set once an undecodable complete event
    is seen. ``edge`` injects the real decoder through ``build_app`` (task 6.1).
    """

    def feed(self, raw: bytes) -> tuple[DownstreamFrame, ...]: ...

    def malformed(self) -> bool: ...


#: The factory that builds a FRESH injected ``SSEDecoder`` per stream (each
#: ``open`` call gets its own decoder so two concurrent streams never share the
#: buffer / latch). ``edge`` wires ``edge/wire/sse.py SSEDecoder`` here.
DecoderFactory = Callable[[], SSEDecoderLike]

#: The factory that builds the httpx async client. Injected so tests / ``edge``
#: control the transport (an ``ASGITransport`` to a ``Fake_Upstream`` locally, a
#: real connection pool in a deployment). Typed as returning the httpx async
#: client under ``TYPE_CHECKING`` only, so the base tree needs no httpx.
if TYPE_CHECKING:
    ClientFactory = Callable[[], httpx.AsyncClient]
else:  # pragma: no cover - runtime alias when httpx is not imported
    ClientFactory = Callable[[], object]

#: The injected monotonic clock (seconds). Accepted and retained for parity with
#: the shipped injected-clock discipline (never read from the wall-clock here).
#: ``abort`` itself reads NO clock and NO bound — the Cancellation_Bound is
#: enforced by the edge ``CancellationController`` wrapping ``abort`` in
#: ``asyncio.wait_for(contract.cancellation_bound_s())`` — so ``dispatch`` holds
#: no capacity literal (R3.2, R9.3).
Clock = Callable[[], float]


class HttpProviderClient:
    """A real streaming HTTP/SSE :class:`ProviderClient` to the Generation_Provider.

    Structurally satisfies the shipped ``@runtime_checkable`` ``ProviderClient``
    Protocol (``open`` + ``async abort``), so ``isinstance(client, ProviderClient)``
    holds and a deployment injects it through ``build_app(provider=...)`` with no
    seam change (R1.1).

    It is a plain SLOTTED class rather than a frozen dataclass — exactly like the
    shipped ``StubProviderClient`` — because the abort latch (``_aborted``) and
    the in-flight response/stream handles are mutable one-way state that
    :meth:`abort` flips and :meth:`open` records. The injected ``client_factory``
    / ``decoder_factory`` / ``clock`` are retained immutably after construction.
    """

    __slots__ = (
        "_aborted",
        "_client_factory",
        "_clock",
        "_decoder_factory",
        "_stream",
    )

    def __init__(
        self,
        *,
        client_factory: ClientFactory,
        decoder_factory: DecoderFactory,
        clock: Clock,
    ) -> None:
        self._client_factory = client_factory
        self._decoder_factory = decoder_factory
        self._clock = clock
        # Mutable one-way abort latch + the in-flight stream handle ``abort``
        # closes/releases. ``_stream`` is the httpx streaming response context for
        # the current ``open`` call (``None`` when no stream is in flight).
        self._aborted = False
        self._stream: ClosableResponse | None = None

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        """Open a streaming HTTP/SSE request and yield ``UpstreamEvent``s (R1).

        A plain (non-``async``) method returning an ``AsyncIterator`` — an async
        generator function has exactly this type — so the caller writes
        ``async for event in provider.open(request): ...`` without an extra
        ``await``, exactly like ``StubProviderClient.open``.

        It (1) unpacks ``request.body`` (the ``Transform.to_provider`` pack —
        ``BODY_MODEL_KEY`` / ``BODY_DELTAS_KEY``) into the OpenAI-compatible JSON
        body REUSING the ``Transform`` key constants (R1.5, R2.3); (2) lazy-imports
        ``httpx`` with the ``pip install 'gateway-v2[conformance]'`` hint (R2.2);
        (3) opens a STREAMING POST to ``request.destination`` (R1.2); (4) feeds
        each received byte chunk into the injected ``SSEDecoder.feed`` and maps
        each decoded ``DownstreamFrame`` to an ``UpstreamEvent`` whose
        ``text_deltas`` are byte-compatible with the ``egress.stream`` shape
        ``tuple[tuple[str, str], ...]`` (R1.3); a decoded error frame becomes a
        terminal ``UpstreamEvent(text_deltas=(), final=True, error_frame=code)``
        so R11 scan-before-forward sees it; the decoder's ``malformed()`` latch
        becomes a fail-closed terminal :data:`STREAM_MALFORMED_UPSTREAM` (R2.3);
        and the terminal event carries ``final=True`` — ``[DONE]`` is clean
        termination (R1.4).
        """
        return self._stream_events(request)

    async def _stream_events(
        self, request: UpstreamRequest
    ) -> AsyncIterator[UpstreamEvent]:
        """The async generator body of :meth:`open` (keeps ``open`` under 120 lines)."""
        self._import_httpx()  # fail fast with the install hint if the extra is absent
        body = self._provider_body(request)
        decoder = self._decoder_factory()

        client = self._client_factory()
        try:
            async with client.stream(
                "POST", request.destination, json=body
            ) as response:
                self._stream = response
                async for event in self._drain_response(response, decoder):
                    yield event
        finally:
            # The stream context closes on normal exit, exhaustion, or an abort:
            # ``abort`` may already have called ``response.aclose()`` out of band
            # (prompt cut of the in-flight read), and the ``async with`` exit then
            # closes it a SECOND time — httpx ``aclose`` is safe to call more than
            # once, so this ``finally`` never double-crashes after an external
            # abort (R3.3). Clearing the handle keeps a later ``abort`` a no-op and
            # lets the client be reopened.
            self._stream = None
            await client.aclose()

    async def _drain_response(
        self,
        response: httpx.Response,
        decoder: SSEDecoderLike,
    ) -> AsyncIterator[UpstreamEvent]:
        """Feed each byte chunk through the decoder and yield mapped events.

        Buffers decoded frames one chunk behind so the LAST content frame of the
        whole stream can carry ``final=True`` (R1.4): a frame is only known to be
        terminal once no further frame follows it and the stream has ended with a
        clean ``[DONE]`` (which yields no frame). A decoder ``malformed()`` latch
        trips a fail-closed terminal error immediately (R2.3). An abort observed
        at a chunk boundary stops the read, and ``abort`` also closes the response
        out of band so a stalled ``aiter_bytes`` is cut promptly (R3.1).
        """
        pending: DownstreamFrame | None = None
        async for raw in response.aiter_bytes():
            if self._aborted:
                return
            frames = decoder.feed(raw)
            if decoder.malformed():
                yield self._malformed_event()
                return
            for frame in frames:
                if frame.error_code is not None:
                    # A mid-stream provider error frame is terminal: surface it so
                    # R11 scan-before-forward sees it before any byte is forwarded.
                    yield self._error_event(frame.error_code)
                    return
                if pending is not None:
                    yield _content_event(pending, final=False)
                pending = frame
        # Clean end of stream (``[DONE]`` yields no frame): the buffered frame, if
        # any, is the terminal content event (R1.4). An empty stream yields nothing.
        if decoder.malformed():
            yield self._malformed_event()
            return
        if pending is not None:
            yield _content_event(pending, final=True)

    async def abort(self) -> None:
        """Cut the in-flight read from the Generation_Provider promptly (R3).

        1. **One-way latch (R3.4).** The ``_aborted`` latch is set first and only
           once: a second ``abort`` observes the already-set latch and returns
           immediately as a no-op — no raise, no double-close — the same one-way
           semantics as the shipped stub and the GW12 ``KillLatch``. The
           ``_drain_response`` loop also observes the latch at the next chunk
           boundary and stops yielding.
        2. **Actively cut the read (R3.1).** Flipping the latch alone would only
           stop the loop at the NEXT chunk — a stalled ``aiter_bytes`` could hang.
           So ``abort`` closes the in-flight httpx streaming response recorded in
           ``self._stream``: ``await response.aclose()`` stops ``aiter_bytes``
           from yielding further bytes, so the real upstream read ceases rather
           than draining to completion. The handle is guarded for both the
           no-stream-in-flight case (``self._stream is None`` → nothing to close)
           and the second-abort case (the latch short-circuits before the close).
        3. **Release the connection (R3.3).** Closing the streaming response hands
           the underlying connection back to the httpx pool; the
           ``_stream_events`` ``finally`` then closes the client cleanly (httpx
           ``aclose`` is safe to call twice, so the ``async with`` exit that races
           this close does not double-crash).

        **Promptness and the bound (R3.2, R9.3).** The Cancellation_Bound is NOT
        read or inlined here — ``dispatch`` holds no capacity literal. The edge
        ``CancellationController._kill_within_bound`` already wraps this call in
        ``asyncio.wait_for(timeout=contract.cancellation_bound_s())`` (the bound =
        ``p99 × 3``, read from the contract) and force-releases on timeout, tying
        into the shipped GW12 ``InFlightKill.CUT_NEXT_CHUNK``. ``abort`` is written
        to return PROMPTLY — it closes the stream handle without awaiting the full
        drain — so that wrapper can honor the bound.
        """
        if self._aborted:
            # Second (or later) abort: the latch is already set and the stream, if
            # any, is already being closed. A no-op — no raise, no double-close.
            return
        self._aborted = True
        stream = self._stream
        if stream is None:
            # No stream in flight (abort before open, or after the stream already
            # ended and ``_stream_events`` cleared the handle): nothing to close.
            return
        # Close the in-flight response promptly: this stops ``aiter_bytes`` and
        # releases the connection back to the httpx pool (R3.1, R3.3). The
        # ``_stream_events`` ``finally`` still runs and may ``aclose`` again, which
        # httpx tolerates (no double-aclose crash).
        await stream.aclose()

    @staticmethod
    def _import_httpx() -> None:
        """Lazy-import ``httpx`` with the install hint (R2.2); never at module load.

        Imported for its side effect — proving the ``conformance`` extra is present
        before the stream is opened — so a missing extra raises a clear
        :class:`ImportError` here rather than an opaque one deep in the stream.
        """
        try:
            import httpx  # noqa: F401  (imported to verify the optional extra is installed)
        except ImportError as exc:  # pragma: no cover - only without the extra
            raise ImportError(
                "httpx is required for the real provider egress client; "
                "install the conformance extra: pip install 'gateway-v2[conformance]'"
            ) from exc

    @staticmethod
    def _provider_body(request: UpstreamRequest) -> dict[str, object]:
        """Unpack ``UpstreamRequest.body`` into the OpenAI-compatible JSON body (R1.5).

        Reuses the shipped ``Transform`` key constants (``BODY_MODEL_KEY`` /
        ``BODY_DELTAS_KEY``) rather than re-shaping by hand, so the pack side
        (``Transform.to_provider``) and this unpack cannot drift. The provider
        reads a streaming completion request, so ``stream`` is set true.
        """
        return {
            "model": request.body.get(BODY_MODEL_KEY, ""),
            BODY_DELTAS_KEY: request.body.get(BODY_DELTAS_KEY, ()),
            "stream": True,
        }

    def _error_event(self, error_code: str) -> UpstreamEvent:
        """A terminal ``UpstreamEvent`` carrying an upstream ``error_frame`` (R1.3)."""
        return UpstreamEvent(text_deltas=(), final=True, error_frame=error_code)

    def _malformed_event(self) -> UpstreamEvent:
        """The fail-closed terminal event for an undecodable upstream SSE (R2.3).

        The decoder's ``malformed()`` latch means the stream was never valid; it is
        surfaced as a terminal ``error_frame`` so the stream is never reported
        successful and ``edge`` renders the declared ``Error_Frame``.
        """
        return self._error_event(STREAM_MALFORMED_UPSTREAM)


def _content_event(frame: DownstreamFrame, *, final: bool) -> UpstreamEvent:
    """Map a decoded content ``DownstreamFrame`` to an ``UpstreamEvent`` (R1.3).

    The decoded frame's ``text_deltas`` are the ``tuple[tuple[str, str], ...]``
    egress transport shape, carried through byte-for-byte into the
    ``UpstreamEvent`` the egress loop consumes (the inverse of
    ``Transform.from_provider``). A content frame carries no ``error_frame``.
    """
    deltas: tuple[tuple[str, str], ...] = frame.text_deltas
    return UpstreamEvent(text_deltas=deltas, final=final)
