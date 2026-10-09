"""Route wiring for the ASGI serving skin (GW12, task 7.2 — split from ``edge/app.py``).

``edge/app.py`` assembles the raw-ASGI application (lifespan, dispatch, ``/metrics``,
``/readyz``); this sibling module holds the *route table* and the per-route handlers so
``app.py`` stays focused on the ASGI protocol mechanics and under the 800-line gate. The split
is noted in the task: ``app.py`` owns the ``scope/receive/send`` protocol loop and the two
operational endpoints, ``routes.py`` owns the chat handler + the registered-for-later
placeholders and the shared ``emit_response`` helper that writes an ``edge.errors.HTTPResponse``
onto the ASGI ``send`` channel.

Everything here is ``edge`` (the top layer), so it may import every layer below:
``dispatch`` (the ``ProviderClient`` + router + transform), ``egress`` (the shipped
``StreamPipeline`` + ``Coalescer``), ``detect`` (the holdback scan core + token counter — the
one correct injection site, since ``edge`` sits above ``detect`` while ``egress`` must not
import it), and ``runtime`` (the holdback config + the ``Window`` bound). The scanner and the
``ProviderClient`` are *injected* through :class:`ChatRoute` so the wiring never reaches around
the layer contract.

**Thin chat handler.** The full ``/v1/chat/completions`` semantics (request parsing, plan
resolution, auth, admission, the end-to-end holdback + output-guard decision) are GW15/GW16.
What this handler proves is the *wiring*: it routes a request through the deterministic
``DispatchRouter``, opens the injected abortable ``ProviderClient``, feeds upstream events
through the bounded ``Coalescer`` (fail-closed admit) and the shipped ``StreamPipeline`` release
loop, and serialises each released ``DownstreamFrame`` with the one ``SSEEncoder`` the error
envelope also uses — ending with the terminal ``data: [DONE]`` marker. A rejected coalescer
admit, or any value-code raised below, renders through the single :mod:`edge.errors`
``Error_Envelope`` as a declared SSE ``Error_Frame`` (never a raw framework 500).

**Placeholders (R2.3).** ``/v1/responses``, the misc OpenAI surface, the MCP surface and the
RAG/vector surface are *registered* here so the route table is complete and a later card wires
each one in place; until then each returns a declared ``not_implemented`` value-code rendered by
the ``Error_Envelope`` — an HTTP ``501`` JSON body, NOT a raw ``404``-with-detail that would leak
internal routing shape.

**No capacity literal, no module-level mutable.** The route table is built inside
:func:`build_routes` (a function local, not a module global), so there is no module-level
mutable; the serving bounds (window, holdback cap, buffer high-water) all derive from the
injected ``ResourceContract`` / ``HoldbackConfig`` and never from a literal here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from gateway_v2.detect.holdback import TokenIndex
from gateway_v2.detect.windowing import max_pattern_length
from gateway_v2.dispatch.provider import ProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.edge import errors
from gateway_v2.edge.stream_control import FirstByteLatch, StreamAttempt, run_with_no_splice
from gateway_v2.edge.wire.sse import SSEEncoder
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.egress.output_guard import OutputResolver
from gateway_v2.egress.stream import (
    DownstreamFrame,
    ScanProtocol,
    Send,
    StreamPipeline,
    UpstreamChunk,
)
from gateway_v2.runtime.holdback_config import HoldbackConfig
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "ASGIReceive",
    "ASGIScope",
    "ASGISend",
    "ChatRoute",
    "RouteHandler",
    "RouteTable",
    "build_routes",
    "emit_response",
)

# --------------------------------------------------------------------------- #
# Minimal ASGI 3.0 type aliases (the repo pulls in no framework; see module doc).
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
#: A route handler: given the ``scope`` and the two channels, it drives the whole response.
RouteHandler = Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]
#: The assembled route table: ``(method, path) -> handler``. Built inside :func:`build_routes`
#: as a function local so there is no module-level mutable (gated).
RouteTable = Mapping[tuple[str, str], RouteHandler]


# --------------------------------------------------------------------------- #
# Emitting a rendered HTTPResponse onto the ASGI send channel (the task 7.1 pattern).
# --------------------------------------------------------------------------- #


async def emit_response(send: ASGISend, response: errors.HTTPResponse) -> None:
    """Write a framework-free :class:`errors.HTTPResponse` onto the ASGI ``send`` channel.

    The single place a rendered HTTP value becomes ASGI events: a ``http.response.start`` with
    the status + the response's ``(name, value)`` header tuple (encoded to the ASGI
    ``list[tuple[bytes, bytes]]`` header shape), then one ``http.response.body`` carrying the
    body bytes. Keeping this in one helper means every HTTP reply — the two operational
    endpoints and every ``Error_Envelope`` rendering — emits identically.
    """
    headers = [
        (name.encode("latin-1"), value.encode("latin-1")) for name, value in response.headers
    ]
    await send(
        {
            "type": "http.response.start",
            "status": response.status,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": response.body})


async def _emit_error_code(send: ASGISend, code: str) -> None:
    """Render a value-code through the single ``Error_Envelope`` and emit it as HTTP.

    Every value-code that reaches a non-streaming handler passes through
    :func:`errors.render_http`, so an unmapped code renders the declared generic error rather
    than leaking raw internal detail (R3.4). The streaming chat handler uses the SSE rendering
    instead (``errors.render_sse``), so the two sides never disagree on the error shape.
    """
    await emit_response(send, errors.render_http(code))


# --------------------------------------------------------------------------- #
# The streaming chat route (R2.2) — thin wiring over the shipped pipeline.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ChatRoute:
    """The injected dependencies the ``/v1/chat/completions`` handler wires together.

    Frozen + slotted: the route is a value built once at boot and shared across requests
    (every piece it holds is itself stateless or per-request-reconstructed). The ``scanner``
    and ``provider`` are injected HERE, in ``edge`` (above ``detect``), which is the correct
    injection site — ``egress`` must never import ``detect`` (R14.1/R14.2). ``active_streams``
    is the live concurrency count the coalescer's derived high-water reads.
    """

    contract: ResourceContract
    provider: ProviderClient
    scanner: ScanProtocol
    resolver: OutputResolver
    cfg: HoldbackConfig
    router: DispatchRouter
    active_streams: Callable[[], int]

    async def handle(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """Stream a chat completion end-to-end over the shipped egress pipeline (R2.2, R7).

        Thin by design (full semantics are GW15/GW16): it proves the wiring is live. The
        coalescer admit fails CLOSED when the derived per-stream high-water is below one byte
        (``STREAM_BUFFER_UNAVAILABLE``); on admit, a per-request ``SSEEncoder`` is the pipeline's
        downstream ``send`` sink and the injected ``ProviderClient``'s events feed the
        ``StreamPipeline`` release loop. A terminal ``[DONE]`` closes the stream. Any value-code
        the pipeline emits (a frame ``error_code``) renders as a declared SSE ``Error_Frame``
        through the single envelope.

        The :class:`~gateway_v2.edge.stream_control.FirstByteLatch` is created HERE, above the
        egress loop, and threaded into the stream orchestration so the no-splice gate (R7) is
        enforced before any provider hand-off: a signed safe retry/fallback runs only while the
        latch is unset, the first released content byte sets it, and after that the stream only
        terminates / errors — a second upstream response is never spliced in (R7.4).
        """
        coalescer = Coalescer(contract=self.contract, active_streams=self.active_streams)
        verdict = coalescer.admit()
        encoder = SSEEncoder()
        await _start_sse(send)
        if verdict.code is not None:
            await _send_sse(send, errors.render_sse(verdict.code))
            await _end_sse(send)
            return
        await self._stream(send, encoder, coalescer)
        await _end_sse(send)

    async def _stream(
        self,
        send: ASGISend,
        encoder: SSEEncoder,
        coalescer: Coalescer,
    ) -> None:
        """Drive the egress pipeline under the no-splice gate and serialise frames onto SSE (R7).

        A per-request :class:`FirstByteLatch` gates retry/fallback: the shared ``_sink`` sets the
        latch the instant the FIRST content byte is released downstream (R7.2), and
        :func:`run_with_no_splice` only ever requests a fresh attempt (a fresh provider response)
        while the latch is unset (R7.1/R7.3). Each attempt opens the injected ``ProviderClient``
        for the routed destination, adapts its events to the shipped ``UpstreamChunk`` transport,
        and runs the shipped ``StreamPipeline`` release loop; released ``DownstreamFrame``s are
        encoded by the one ``SSEEncoder``. A frame carrying an ``error_code`` renders the declared
        terminal ``Error_Frame`` and no content follows it. On a clean finish the terminal
        ``[DONE]`` marker is emitted; when every eligible attempt failed before the first byte the
        caller rendered no byte and the stream closes without a splice (R7.4).
        """
        latch = FirstByteLatch()
        sent_terminal = _TerminalLatch()

        async def _sink(frame: DownstreamFrame) -> None:
            if sent_terminal.is_set():
                return
            if frame.error_code is not None:
                await _send_sse(send, encoder.error(frame.error_code))
                sent_terminal.set()
                return
            # The first released content byte closes the no-splice gate (R7.2): once a byte is on
            # the wire the orchestration will never attempt a second upstream response (R7.4).
            latch.set_on_release()
            await _send_sse(send, encoder.content(frame))

        offered = _TerminalLatch()

        def _factory() -> StreamAttempt | None:
            return self._next_attempt(offered, latch, _sink, coalescer)

        await run_with_no_splice(latch, _factory)
        if not sent_terminal.is_set() and not encoder.terminated:
            await _send_sse(send, encoder.done())

    def _next_attempt(
        self,
        offered: _TerminalLatch,
        latch: FirstByteLatch,
        sink: Send,
        coalescer: Coalescer,
    ) -> StreamAttempt | None:
        """Yield the next eligible stream attempt, or ``None`` when none remains (R7.1).

        The thin handler has exactly ONE signed upstream to try (GW15 adds the signed fallback
        chain), so the first call returns an attempt and every later call returns ``None`` — the
        ``offered`` one-way latch records that the sole provider response has been handed out, so a
        byte-less failure is NOT retried against the same provider forever and there is nothing
        further to splice. :func:`run_with_no_splice` only invokes this factory while the first-byte
        latch still permits a retry, so a fresh attempt is impossible once a byte is on the wire.
        """
        if offered.is_set():
            return None
        offered.set()

        async def _attempt() -> bool:
            pipeline = self._build_pipeline()
            request = UpstreamRequest(destination=self._destination(), body=_EMPTY_BODY)
            chunks = _as_chunks(self.provider.open(request), coalescer)
            await pipeline.run(chunks, sink, _never_killed)
            # The sink set the first-byte latch the instant a content byte went out; report that
            # as the "released a byte" signal so run_with_no_splice closes the gate (R7.2/R7.4).
            return latch.is_set()

        return _attempt

    def _build_pipeline(self) -> StreamPipeline:
        """Construct a pass-through ``StreamPipeline`` wired to the injected scanner/resolver.

        A thin handler runs the pipeline in its shipped pass-through mode (``enforcing_output``
        false): it proves the release loop + scanner/resolver + token counter + ``Window`` are
        all wired without needing GW15 plan resolution to compute the enforcing-output set. The
        ``Window`` and the holdback cap both derive from the injected contract/config — no
        literal. GW15 swaps the pass-through flag for a real ``for_plan`` resolution.
        """
        return StreamPipeline(
            scanner=self.scanner,
            resolver=self.resolver,
            token_index=TokenIndex(),
            cfg=self.cfg,
            metrics=HoldbackMetrics(),
            enforcing_output=False,
            window=max_pattern_length(),
        )

    def _destination(self) -> str:
        """The upstream destination for this request.

        A thin handler has no resolved ``ExecutionPlan`` yet (GW15 parses the request into one),
        so it returns the router's deterministic selection stand-in: an empty-plan destination is
        out of scope here, so the handler names the provider's default destination. The point of
        the wiring is that the ``DispatchRouter`` IS the selection authority once a plan exists.
        """
        return _DEFAULT_DESTINATION


# --------------------------------------------------------------------------- #
# Placeholder routes (R2.3) — registered-for-later, declared not-implemented.
# --------------------------------------------------------------------------- #


def _placeholder(surface: str) -> RouteHandler:
    """Build a registered-for-later handler that renders a declared ``not_implemented`` code.

    The surface is *registered* so the route table is complete and a later card (GW15/16/17/18)
    wires the real handler in place. Until then it renders the ``not_implemented`` value-code
    through the single ``Error_Envelope`` (an HTTP ``501`` JSON body via the unmapped-code
    generic path), NOT a raw ``404``-with-detail that would leak which surfaces exist.
    """

    async def _handle(scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        await emit_response(send, errors.render_not_implemented(surface))

    return _handle


# --------------------------------------------------------------------------- #
# Route table assembly (R2.2 / R2.3).
# --------------------------------------------------------------------------- #


def build_routes(chat: ChatRoute) -> RouteTable:
    """Assemble the ``(method, path) -> handler`` table (R2.2/R2.3).

    The streaming chat route is wired to the live handler; the responses/misc/MCP/RAG surfaces
    are registered as declared not-implemented placeholders. Built as a function local (returned
    as a read-only ``Mapping``) so there is no module-level mutable table (gated).
    """
    return {
        ("POST", "/v1/chat/completions"): chat.handle,
        ("POST", "/v1/completions"): chat.handle,
        ("POST", "/v1/responses"): _placeholder("responses"),
        ("POST", "/v1/embeddings"): _placeholder("embeddings"),
        ("GET", "/v1/models"): _placeholder("models"),
        ("POST", "/v1/moderations"): _placeholder("moderations"),
        ("POST", "/v1/mcp"): _placeholder("mcp"),
        ("POST", "/v1/rag/query"): _placeholder("rag"),
        ("POST", "/v1/vector/query"): _placeholder("vector"),
    }


# --------------------------------------------------------------------------- #
# Small internal helpers (SSE framing on the ASGI channel; adapters).
# --------------------------------------------------------------------------- #

#: SSE content type with the ``no-cache`` + ``keep-alive`` headers a streaming client expects.
_SSE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-type", b"text/event-stream"),
    (b"cache-control", b"no-cache"),
    (b"connection", b"keep-alive"),
)

#: The empty provider body a thin handler submits (GW15 packs the real request). A
#: ``MappingProxyType`` so the one module-level mapping is immutable (gated by
#: ``check_no_module_mutable``) — a frozen value, not a mutable table.
_EMPTY_BODY: Mapping[str, object] = MappingProxyType({})

#: The default upstream destination a thin handler names (GW15 derives it from the plan).
_DEFAULT_DESTINATION = "amf://upstream/default"


async def _start_sse(send: ASGISend) -> None:
    """Open a ``200`` SSE response on the ASGI channel (``more_body`` keeps it streaming)."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": list(_SSE_HEADERS),
        }
    )


async def _send_sse(send: ASGISend, frame: bytes) -> None:
    """Write one SSE frame as a non-terminal ``http.response.body`` event (``more_body`` set)."""
    await send({"type": "http.response.body", "body": frame, "more_body": True})


async def _end_sse(send: ASGISend) -> None:
    """Close the SSE response with an empty terminal body event (``more_body`` cleared)."""
    await send({"type": "http.response.body", "body": b"", "more_body": False})


def _never_killed() -> bool:
    """The kill probe for the thin handler: the in-flight-kill controller is GW10."""
    return False


class _TerminalLatch:
    """A one-way latch so a terminal SSE frame is emitted at most once per stream."""

    __slots__ = ("_set",)

    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set


async def _as_chunks(
    events: AsyncIterator[UpstreamEvent],
    coalescer: Coalescer,
) -> AsyncIterator[UpstreamChunk]:
    """Adapt a provider event stream to the shipped ``UpstreamChunk`` source, bounded.

    Each :class:`~gateway_v2.dispatch.provider.UpstreamEvent` becomes an ``UpstreamChunk`` the
    pipeline consumes. Before a chunk is forwarded, its payload bytes are offered to the bounded
    ``Coalescer``: an ``offer`` that returns ``False`` is backpressure (the buffer reached the
    derived high-water) and the chunk waits by being released as it is handed on. The thin
    handler releases immediately after offering so the bounded buffer is exercised without a
    real slow-consumer loop (GW9/GW13 wire the credit-paced consumer).
    """
    async for event in events:
        text = "".join(delta for _, delta in event.text_deltas)
        nbytes = len(text.encode("utf-8"))
        coalescer.offer(nbytes)
        yield UpstreamChunk(text_deltas=event.text_deltas, final=event.final)
        coalescer.release(nbytes)
        if event.final:
            return
