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
from gateway_v2.edge.cancel import InFlightControl, InFlightControlSource, KillLatch
from gateway_v2.edge.error_frame_scan import ErrorFrameScanner, scan_error_frame
from gateway_v2.edge.stream_control import FirstByteLatch, StreamAttempt, run_with_no_splice
from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts
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

    ``error_scanner`` is the findings-producing scanner for a mid-stream provider error frame
    (R11): also injected HERE, above ``detect``, so the error-frame scan step
    (:func:`~gateway_v2.edge.error_frame_scan.scan_error_frame`) can run the real detector
    through the injected seam while ``egress`` stays free of a ``detect`` import. It produces
    completed findings over an error frame's whole text; the handler resolves them through the
    same ``resolver`` + ``apply_decision`` the content path uses.
    """

    contract: ResourceContract
    provider: ProviderClient
    scanner: ScanProtocol
    resolver: OutputResolver
    cfg: HoldbackConfig
    router: DispatchRouter
    active_streams: Callable[[], int]
    control: InFlightControlSource
    clock: Callable[[], float]
    error_scanner: ErrorFrameScanner

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
        """Drive the egress pipeline under the no-splice gate + in-flight control (R7, R10).

        A per-request :class:`FirstByteLatch` gates retry/fallback: the shared ``_sink`` sets the
        latch the instant the FIRST content byte is released downstream (R7.2), and
        :func:`run_with_no_splice` only ever requests a fresh attempt (a fresh provider response)
        while the latch is unset (R7.1/R7.3). Each attempt opens the injected ``ProviderClient``
        for the routed destination, adapts its events to the shipped ``UpstreamChunk`` transport,
        and runs the shipped ``StreamPipeline`` release loop; released ``DownstreamFrame``s are
        encoded by the one ``SSEEncoder``.

        **In-flight control (R10).** A per-request :class:`KillLatch` replaces the old
        always-false ``_never_killed`` probe and is handed straight to ``pipeline.run`` as the
        ``killed()`` seam. An :class:`InFlightControl`, built over the injected control source and
        the derived bounds, is evaluated at EACH chunk boundary (in :func:`_as_chunks`): a fired
        trigger (max duration, kill switch, key revocation, plan change, or a stale/unavailable
        snapshot that fails closed) flips the SAME latch with its posture code and stops the chunk
        source, so no further upstream bytes are forwarded after the cut (R10.7). Because the thin
        handler runs the pipeline in pass-through mode (which does not itself render a terminal
        frame), the handler reads :meth:`KillLatch.reason` after the run and emits the matching
        declared ``Error_Frame`` (``STREAM_MAX_DURATION`` / ``STREAM_KILLED`` /
        ``STREAM_KEY_REVOKED`` / ``STREAM_PLAN_CHANGED`` / ``STREAM_SNAPSHOT_STALE``) itself.

        **Bounded timeouts (C27 / R9).** A per-request :class:`StreamTimeouts` wraps the serving
        loop's two awaits with the three derived C27 bounds — a sibling cut to the R10 triggers
        that flips the SAME :class:`KillLatch` so the terminal frame renders at the ONE terminal
        site. The inter-chunk bound (R9.2) wraps the provider's next-event await in
        :func:`_as_chunks`; the write (R9.4) + push-only idle (R9.3) bounds wrap the downstream
        ``send`` in ``_sink``. A breach flips the latch with its code and raises
        :class:`StreamTimeout`, which the no-splice driver surfaces as a byte-less-or-terminal
        failure; :meth:`_finish_stream` then renders the latch's timeout code as the declared
        ``Error_Frame`` (``STREAM_INTER_CHUNK_TIMEOUT`` / ``STREAM_IDLE_TIMEOUT`` /
        ``STREAM_WRITE_TIMEOUT``).

        On a clean finish the terminal ``[DONE]`` marker is emitted; when every eligible attempt
        failed before the first byte the caller rendered no byte and the stream closes without a
        splice (R7.4).
        """
        latch = FirstByteLatch()
        sent_terminal = _TerminalLatch()
        kill_latch = KillLatch()
        control = InFlightControl(
            source=self.control,
            contract=self.contract,
            clock=self.clock,
            kill_latch=kill_latch,
        )
        timeouts = StreamTimeouts(
            contract=self.contract,
            kill_latch=kill_latch,
            clock=self.clock,
        )

        async def _sink(frame: DownstreamFrame) -> None:
            if sent_terminal.is_set():
                return
            error_code = frame.error_code
            if error_code is not None:
                # The terminal error write is bounded by the write/idle timeout too (R9.3/R9.4)
                # so a client that stops reading cannot wedge the stream on the terminal frame.
                await timeouts.send(lambda: _send_sse(send, encoder.error(error_code)))
                sent_terminal.set()
                return
            # The first released content byte closes the no-splice gate (R7.2): once a byte is on
            # the wire the orchestration will never attempt a second upstream response (R7.4).
            latch.set_on_release()
            # Each downstream content write is bounded by the Write_Timeout, and the inter-write
            # gap by the Idle_Timeout (R9.3/R9.4); a breach flips the kill latch + raises.
            await timeouts.send(lambda: _send_sse(send, encoder.content(frame)))

        offered = _TerminalLatch()

        def _factory() -> StreamAttempt | None:
            return self._next_attempt(
                offered, latch, _sink, coalescer, kill_latch, control, timeouts
            )

        await self._run_attempts(latch, _factory)
        await self._finish_stream(send, encoder, kill_latch, sent_terminal)

    async def _run_attempts(
        self,
        latch: FirstByteLatch,
        factory: Callable[[], StreamAttempt | None],
    ) -> None:
        """Drive the no-splice attempt loop, swallowing a timeout cut as a terminal failure (R9).

        :func:`run_with_no_splice` re-raises a post-first-byte failure (R7.3). A C27 timeout is a
        legitimate terminal cut, not a splice candidate: it has already flipped the per-request
        :class:`KillLatch` with its posture code, so the stream must terminate and render that
        code — never retry. Catching :class:`StreamTimeout` here lets the loop fall through to
        :meth:`_finish_stream`, which reads the latch reason and emits the declared timeout
        ``Error_Frame`` at the single terminal site (R9.2–R9.4, fail closed: the resources are
        released as the loop unwinds).
        """
        try:
            await run_with_no_splice(latch, factory)
        except StreamTimeout:
            # The latch already carries the timeout reason; _finish_stream renders it. The raise
            # exists only to unwind the stalled await — resources release as the loop exits.
            return

    async def _finish_stream(
        self,
        send: ASGISend,
        encoder: SSEEncoder,
        kill_latch: KillLatch,
        sent_terminal: _TerminalLatch,
    ) -> None:
        """Close the stream: render an in-flight cut's ``Error_Frame`` or the ``[DONE]`` marker.

        If an in-flight trigger cut the stream (R10) OR a C27 timeout tripped (R9.2-R9.4), the
        per-request :class:`KillLatch` carries the reason posture code the FIRST cut set; the
        handler renders it as the declared terminal ``Error_Frame`` here (the pass-through
        pipeline does not emit one of its own), so a max-duration cut shows ``STREAM_MAX_DURATION``,
        a key revocation shows ``STREAM_KEY_REVOKED`` (R10.2-R10.6), and a stall shows
        ``STREAM_INTER_CHUNK_TIMEOUT`` / ``STREAM_IDLE_TIMEOUT`` / ``STREAM_WRITE_TIMEOUT``
        (R9.2-R9.4) — one terminal site for every cut reason. No content frame follows the terminal
        frame (``sent_terminal`` guards against a double terminal). Only when nothing was cut and no
        terminal has been emitted does the clean ``[DONE]`` marker close the stream.

        The in-loop content/error writes in ``_sink`` are the ones bounded by the Write_Timeout +
        Idle_Timeout (R9.3/R9.4); this terminal close is a best-effort final marker emitted after
        the resources have already been released by the unwinding loop, so it uses the plain
        ``_send_sse`` and never re-raises a timeout past the handler.
        """
        if sent_terminal.is_set() or encoder.terminated:
            return
        reason = kill_latch.reason()
        if reason is not None:
            await _send_sse(send, encoder.error(reason))
            sent_terminal.set()
            return
        await _send_sse(send, encoder.done())

    def _next_attempt(
        self,
        offered: _TerminalLatch,
        latch: FirstByteLatch,
        sink: Send,
        coalescer: Coalescer,
        kill_latch: KillLatch,
        control: InFlightControl,
        timeouts: StreamTimeouts,
    ) -> StreamAttempt | None:
        """Yield the next eligible stream attempt, or ``None`` when none remains (R7.1).

        The thin handler has exactly ONE signed upstream to try (GW15 adds the signed fallback
        chain), so the first call returns an attempt and every later call returns ``None`` — the
        ``offered`` one-way latch records that the sole provider response has been handed out, so a
        byte-less failure is NOT retried against the same provider forever and there is nothing
        further to splice. :func:`run_with_no_splice` only invokes this factory while the first-byte
        latch still permits a retry, so a fresh attempt is impossible once a byte is on the wire.

        The attempt hands the per-request :class:`KillLatch` to ``pipeline.run`` as the ``killed()``
        seam and threads the :class:`InFlightControl` + :class:`StreamTimeouts` into
        :func:`_as_chunks`, which evaluates every R10 trigger at each chunk boundary (R10.2-R10.7)
        and bounds the next-upstream-event await by the Inter_Chunk_Timeout (R9.2).
        """
        if offered.is_set():
            return None
        offered.set()

        async def _attempt() -> bool:
            pipeline = self._build_pipeline()
            request = UpstreamRequest(destination=self._destination(), body=_EMPTY_BODY)
            chunks = _as_chunks(
                self.provider.open(request),
                coalescer,
                control,
                timeouts,
                error_scan=_ErrorFrameScan(
                    scanner=self.error_scanner,
                    resolver=self.resolver,
                    kill_latch=kill_latch,
                ),
            )
            await pipeline.run(chunks, sink, kill_latch)
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


class _TerminalLatch:
    """A one-way latch so a terminal SSE frame is emitted at most once per stream."""

    __slots__ = ("_set",)

    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set


@dataclass(frozen=True, slots=True)
class _ErrorFrameScan:
    """The injected glue the chunk adapter needs to scan a mid-stream error frame (R11).

    Bundles the three pieces :func:`_as_chunks` threads into the error-frame scan step so the
    adapter's signature stays small: the findings ``scanner`` and the ``resolver`` (both injected
    into :class:`ChatRoute` above ``detect``, so ``egress`` never imports ``detect`` — R14.1) and
    the per-request :class:`KillLatch` a WITHHELD frame flips with its terminal posture code. The
    handler's :meth:`ChatRoute._finish_stream` then renders that code as the declared
    ``Error_Frame`` at the single terminal site — the same seam a timeout / in-flight cut uses.
    Frozen + slotted: a per-attempt value, not mutable state.
    """

    scanner: ErrorFrameScanner
    resolver: OutputResolver
    kill_latch: KillLatch


def _forward_error_frame(frame: str, error_scan: _ErrorFrameScan) -> str | None:
    """Scan a mid-stream error frame; return the bytes to forward, or ``None`` to withhold (R11).

    Runs the single whole-text scan + decision via
    :func:`~gateway_v2.edge.error_frame_scan.scan_error_frame` (byte-linear, R11.5). A REDACT/ALLOW
    outcome returns the frame text with the decided redactions applied — the only bytes of the
    frame that ever leave the gateway (R11.2). A WITHHELD outcome (BLOCK → ``output_blocked``,
    R11.3; scan error → ``scan_failure``, R11.4, fail closed) flips the shared :class:`KillLatch`
    with the terminal posture code and returns ``None``, so the caller stops the source and the
    handler renders the declared ``Error_Frame`` from the latch reason. No raw error-frame byte is
    forwarded on a withhold (R15.1/R15.2).
    """
    outcome = scan_error_frame(
        frame,
        scanner=error_scan.scanner,
        resolver=error_scan.resolver,
    )
    if outcome.withheld_code is not None:
        error_scan.kill_latch.kill(outcome.withheld_code)
        return None
    return outcome.forward_text


async def _as_chunks(
    events: AsyncIterator[UpstreamEvent],
    coalescer: Coalescer,
    control: InFlightControl,
    timeouts: StreamTimeouts,
    *,
    error_scan: _ErrorFrameScan,
) -> AsyncIterator[UpstreamChunk]:
    """Adapt a provider event stream to the shipped ``UpstreamChunk`` source, bounded + cut-aware.

    Each :class:`~gateway_v2.dispatch.provider.UpstreamEvent` becomes an ``UpstreamChunk`` the
    pipeline consumes. Before a chunk is forwarded, its payload bytes are offered to the bounded
    ``Coalescer``: an ``offer`` that returns ``False`` is backpressure (the buffer reached the
    derived high-water) and the chunk waits by being released as it is handed on. The thin
    handler releases immediately after offering so the bounded buffer is exercised without a
    real slow-consumer loop (GW9/GW13 wire the credit-paced consumer).

    **In-flight control at the chunk boundary (R10.2-R10.7).** Each yielded ``UpstreamChunk`` IS a
    chunk boundary, so the :class:`InFlightControl` is evaluated here, BEFORE the next chunk is
    forwarded. A fired trigger (max duration, kill switch, key revocation, plan change, or a
    stale/unavailable snapshot that fails closed) flips the shared :class:`KillLatch` with its
    posture code; this adapter then stops the source (``return``), so no further upstream chunk
    bytes are forwarded to the caller after the cut (R10.7). The cut therefore takes effect no
    later than the next chunk boundary (``InFlightKill.CUT_NEXT_CHUNK``), within the derived
    ``max_cut_latency_s()`` budget. The pass-through pipeline does not render a terminal frame on a
    killed source, so the handler reads the latch reason afterwards and emits the matching
    ``Error_Frame`` (see :meth:`ChatRoute._finish_stream`).

    **Inter-chunk timeout at the same boundary (R9.2).** The ``await`` on the provider's NEXT
    event is wrapped by :meth:`StreamTimeouts.next_event` in ``asyncio.wait_for`` with the derived
    ``inter_chunk_timeout_s()`` (no literal, R9.5). If the next upstream chunk does not arrive in
    time, the stalled read is cancelled, the shared :class:`KillLatch` is flipped with
    ``STREAM_INTER_CHUNK_TIMEOUT``, and :class:`StreamTimeout` propagates out of this generator so
    the pipeline + serving loop unwind and release resources — the handler then renders the
    declared ``Error_Frame`` from the latch reason (R9.2, fail closed). The iterator is driven by
    an explicit ``__anext__`` so the per-event await is the thing bounded; ``StopAsyncIteration``
    ends the stream normally.

    **Mid-stream error-frame scanning (R11).** A provider event whose ``error_frame`` is set
    carries an untrusted free-text error body that can hold a secret (a connection string, a
    token in a stack trace). BEFORE any byte of it is forwarded (R11.1) it is scanned through
    :func:`~gateway_v2.edge.error_frame_scan.scan_error_frame` (the injected findings ``scanner``
    + the same ``resolver`` / ``apply_decision`` the content path uses). The outcome drives one
    of two paths: a REDACT/ALLOW decision forwards the frame with the decided redactions applied
    as a FINAL content ``UpstreamChunk`` (R11.2 — the only bytes of the frame that ever leave the
    gateway), while a BLOCK (R11.3) or a scan error (R11.4, fail closed) withholds the frame
    entirely, flips the shared :class:`KillLatch` with its terminal posture code
    (``output_blocked`` / ``scan_failure``), and stops the source so no byte is forwarded — the
    handler then renders the declared ``Error_Frame`` from the latch reason. The scan is a single
    whole-text pass (byte-linear, R11.5). An error frame is terminal either way, so the adapter
    returns after handling it.
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
            forwarded = _forward_error_frame(event.error_frame, error_scan)
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
