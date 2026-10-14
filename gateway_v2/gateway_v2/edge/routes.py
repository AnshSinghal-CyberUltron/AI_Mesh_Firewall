"""Route wiring for the ASGI serving skin (GW12, task 7.2 — split from ``edge/app.py``).

``edge/app.py`` assembles the raw-ASGI application (lifespan, dispatch, ``/metrics``,
``/readyz``); this sibling module holds the *route table* and the per-route handlers so
``app.py`` stays focused on the ASGI protocol mechanics and under the 800-line gate. The split
is noted in the task: ``app.py`` owns the ``scope/receive/send`` protocol loop and the two
operational endpoints, ``routes.py`` owns the chat handler + the registered-for-later
placeholders and the shared ``emit_response`` helper that writes an ``edge.errors.HTTPResponse``
onto the ASGI ``send`` channel.

Everything here is ``edge`` (the top layer), so it imports every layer below; the scanner, the
input detector, and the ``ProviderClient`` are *injected* through :class:`ChatRoute` (``edge``
sits above ``detect`` while ``egress`` must not import it), so the wiring never reaches around
the layer contract.

**The real chat handler (GW15).** :meth:`ChatRoute.handle` runs the full §10.4 request lifecycle
(cheap pre-checks → SDK parse → auth → the ONE plan round trip → admission → budget → the INPUT
scan with a BLOCK short-circuit) through a per-request
:class:`~gateway_v2.edge.openai_chat.ChatSurface` BEFORE any provider is opened — a refusal
makes zero provider calls. On a proceed outcome it dispatches on the parsed ``streaming``
disposition: a streaming request runs the shipped egress orchestration below with the
plan-derived destination + the transformed body + plan-aware output enforcement; a
non-streaming request collects the whole upstream, enforces it, and renders ONE OpenAI JSON via
``ChatSurface.collect_and_render``. Every refusal — pre-parse, post-parse, or an in-stream cut —
renders through the single :mod:`edge.errors` ``Error_Envelope`` (an HTTP body for a
non-streaming request, a declared SSE ``Error_Frame`` for a streaming one), never a raw
framework 500. The ``ChatSurface`` is built per call (``ChatSurface(deps=self)``) and every
latch / in-flight / timeout value is per-request, so nothing request-scoped is shared on the
frozen :class:`ChatRoute` (two-tenant isolation, R13).

The GW15 §10.4 dependency surfaces + their fail-closed defaults live in the sibling
:mod:`gateway_v2.edge.chat_deps`, imported here.

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

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from gateway_v2.detect.holdback import TokenIndex
from gateway_v2.detect.windowing import max_pattern_length
from gateway_v2.dispatch.provider import ProviderClient, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.domain.posture import PLAN_UNAVAILABLE
from gateway_v2.edge import errors
from gateway_v2.edge.asgi_sse import (
    ASGIReceive,
    ASGIScope,
    ASGISend,
)
from gateway_v2.edge.asgi_sse import ErrorFrameScan as _ErrorFrameScan
from gateway_v2.edge.asgi_sse import TerminalLatch as _TerminalLatch
from gateway_v2.edge.asgi_sse import as_chunks as _as_chunks
from gateway_v2.edge.asgi_sse import end_sse as _end_sse
from gateway_v2.edge.asgi_sse import read_body as _read_body
from gateway_v2.edge.asgi_sse import send_sse as _send_sse
from gateway_v2.edge.asgi_sse import start_sse as _start_sse
from gateway_v2.edge.cancel import InFlightControl, InFlightControlSource, KillLatch
from gateway_v2.edge.chat_deps import (
    AdmissionDecider,
    BudgetDecider,
    InputDetector,
    fail_closed_admission,
    fail_closed_budget,
    no_findings_detector,
    unresolved_snapshot_source,
)
from gateway_v2.edge.error_frame_scan import ErrorFrameScanner
from gateway_v2.edge.executor import ScanExecutor
from gateway_v2.edge.identity_resolver import IdentityResolver, fail_closed_resolver
from gateway_v2.edge.openai_misc import (
    EmbeddingsSurface,
    MiscRoute,
    ModerationsSurface,
)
from gateway_v2.edge.openai_misc_models import ModelsSurface
from gateway_v2.edge.stream_control import FirstByteLatch, StreamAttempt, run_with_no_splice
from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts
from gateway_v2.edge.wire.openai import (
    CanonicalRequest,
    OpenAIRequestParser,
    OpenAIResponseRenderer,
)
from gateway_v2.edge.wire.sse import SSEEncoder
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.egress.output_guard import OutputResolver
from gateway_v2.egress.stream import (
    DownstreamFrame,
    EnforcementUndetermined,
    ScanProtocol,
    Send,
    StreamPipeline,
)
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.runtime.holdback_config import HoldbackConfig
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.stream_metrics import PerRequestExports

__all__ = (
    "ASGIReceive",
    "ASGIScope",
    "ASGISend",
    "AdmissionDecider",
    "BudgetDecider",
    "ChatRoute",
    "InputDetector",
    "MiscRoute",
    "RouteHandler",
    "RouteTable",
    "build_routes",
    "emit_response",
)

# --------------------------------------------------------------------------- #
# Route-table type aliases (the ASGI 3.0 primitives live in ``edge.asgi_sse``).
# --------------------------------------------------------------------------- #

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
# The chat route (R2.2) — the real GW15 handler over the shipped egress pipeline.
# --------------------------------------------------------------------------- #
#
# The GW15 §10.4 dependency SURFACES the lifecycle reads — the ``AdmissionDecider`` /
# ``BudgetDecider`` narrow protocols, the ``InputDetector`` callable shape, and their fail-closed
# default implementations (``fail_closed_admission`` / ``fail_closed_budget`` /
# ``no_findings_detector`` / ``unresolved_snapshot_source``) — live in the sibling
# :mod:`gateway_v2.edge.chat_deps` and are imported above. ``ChatRoute`` carries each as a
# fail-closed default so every existing construction keeps compiling until a boot card injects the
# real GW19 / GW06 / GW07-08 components; an unwired route sheds at admission (zero provider calls)
# before the budget or input phases ever matter.


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

    ``executor`` is the C38 serving-loop-discipline seam (``ScanExecutor``, sized by
    ``ResourceContract``): the CPU-bound error-frame scan is handed to the GIL-releasing executor
    so one stream's scan cannot starve another's latency (R13.1/R13.3) and serving-loop lag is
    recorded as the R13.4 SLO input. (The shipped per-delta holdback scan stays synchronous in the
    pure release loop — offloading it is a GW13 follow-up.)
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
    executor: ScanExecutor
    # --- GW15 §10.4 lifecycle deps ---
    #
    # The seams the ``ChatSurface`` lifecycle reads, each a fail-closed default defined in
    # ``edge/chat_deps.py`` (an unwired route refuses — admission sheds first, zero provider
    # calls). ``admission`` / ``quota`` are the narrow ``AdmissionDecider`` / ``BudgetDecider``
    # protocols the shipped ``AdmissionController`` / ``QuotaComponent`` satisfy structurally.
    # Every field is stateless or per-request-rebuilt, so the frozen route carries no
    # request-scoped state (two-tenant isolation, R13).
    parser: OpenAIRequestParser = OpenAIRequestParser()
    identity: IdentityResolver = fail_closed_resolver
    snapshot_source: Callable[[], ReplicaSnapshot] = unresolved_snapshot_source
    renderer: OpenAIResponseRenderer = OpenAIResponseRenderer()
    admission: AdmissionDecider = fail_closed_admission
    quota: BudgetDecider = fail_closed_budget
    input_detector: InputDetector = no_findings_detector
    transform: Transform = Transform()
    #: The GW14d per-request telemetry producer (R14). Every terminal outcome
    #: (allowed / shed / budget-refused / input-blocked / output-redacted /
    #: output-blocked / errored) populates its admission / budget / input- /
    #: output-enforcement dimensions through this one producer; ``fail_open_total``
    #: is pinned at zero and no raw request/response content is ever placed in an
    #: export field (R14.1-R14.4). It defaults to a fresh :class:`PerRequestExports`
    #: so every existing construction keeps compiling until the boot card
    #: (``edge/app.py``, task 6.1) injects the process producer; the boot wiring
    #: replaces this default and threads the dimensions on each terminal outcome.
    exports: PerRequestExports = PerRequestExports()

    async def handle(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """Serve a chat/completions request end-to-end (R2.2, R7, R10, §10.4).

        Assembles the request ``body`` from the ASGI ``receive`` channel, then runs the full
        §10.4 pre-provider lifecycle through a per-request
        :class:`~gateway_v2.edge.openai_chat.ChatSurface` (``ChatSurface(deps=self)``): cheap
        pre-checks → SDK parse → auth → the ONE plan round trip → admission → budget → the INPUT
        scan with a BLOCK short-circuit. Every phase runs BEFORE any provider is opened, so a
        refusal makes zero provider calls (R7.2).

        * **Refuse** (``outcome.refuse_code`` set): render the declared value-code through the
          single ``edge/errors`` ``Error_Envelope`` and stop — a pre-parse refusal (``req is
          None``: a malformed / oversized body is not a stream yet) renders as an HTTP body, a
          post-parse refusal renders as a declared SSE ``Error_Frame`` when the parsed request
          asked to stream (``req.streaming``) and otherwise as an HTTP body (R12.1).
        * **Proceed** (``refuse_code is None``): dispatch on the parsed ``streaming`` disposition
          — a streaming request runs the shipped egress orchestration (:meth:`_stream`) with the
          plan-derived ``destination``, the transformed ``body``, and plan-aware output enforcement
          (``StreamPipeline.for_plan``); a non-streaming request collects the whole upstream,
          enforces it, and renders ONE OpenAI JSON via ``ChatSurface.collect_and_render`` (R9).

        The ``ChatSurface`` and every per-request latch / in-flight / timeout value are built here,
        per call, so nothing request-scoped is shared on the frozen route (two-tenant isolation,
        R13). ``ChatSurface`` is imported inside the method because ``edge/openai_chat.py`` imports
        from THIS module at module level — a top-level import of it here would cycle.
        """
        from gateway_v2.edge.openai_chat import ChatSurface

        body = await _read_body(receive)
        req, outcome = await ChatSurface(deps=self).run_lifecycle(scope, body)
        if outcome.refuse_code is not None:
            await self._render_refusal(send, req, outcome.refuse_code)
            return
        # Proceed: the lifecycle guarantees all three provider inputs are set on a non-refusal.
        plan = outcome.plan
        destination = outcome.destination
        upstream_body = outcome.body
        assert plan is not None and destination is not None and upstream_body is not None
        assert req is not None  # parse succeeded on any proceed outcome
        if req.streaming:
            await self._stream_response(send, plan, destination, upstream_body)
            return
        await ChatSurface(deps=self).collect_and_render(
            req, plan, destination, upstream_body, send
        )

    async def _render_refusal(
        self, send: ASGISend, req: CanonicalRequest | None, code: str
    ) -> None:
        """Render a §10.4 refusal through the single ``Error_Envelope`` (R12.1).

        A pre-parse refusal (``req is None`` — a malformed / oversized body never became a stream)
        renders as an HTTP body; a post-parse refusal renders as a declared SSE ``Error_Frame``
        when the parsed request asked to stream (``req.streaming``) and otherwise as an HTTP body.
        Either way the one ``edge/errors`` envelope maps the value-code, so an unmapped code still
        renders a declared generic error rather than a raw framework 500.
        """
        if req is not None and req.streaming:
            await _start_sse(send)
            await _send_sse(send, errors.render_sse(code))
            await _end_sse(send)
            return
        await _emit_error_code(send, code)

    async def _stream_response(
        self,
        send: ASGISend,
        plan: ExecutionPlan,
        destination: str,
        body: Mapping[str, object],
    ) -> None:
        """Open the SSE response + drive the shipped egress orchestration (R2.2, R7, R10).

        Reached only on a proceed outcome for a streaming request. The coalescer admit fails
        CLOSED when the derived per-stream high-water is below one byte
        (``STREAM_BUFFER_UNAVAILABLE``); on admit, a per-request ``SSEEncoder`` is the pipeline's
        downstream ``send`` sink and the injected ``ProviderClient``'s events — opened to the
        plan-derived ``destination`` with the transformed ``body`` — feed the ``StreamPipeline``
        release loop built via ``for_plan(plan, ...)`` so output enforcement reflects the resolved
        plan. A terminal ``[DONE]`` closes the stream; any value-code the pipeline emits renders as
        a declared SSE ``Error_Frame`` through the single envelope.

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
        await self._stream(send, encoder, coalescer, plan, destination, body)
        await _end_sse(send)

    async def _stream(
        self,
        send: ASGISend,
        encoder: SSEEncoder,
        coalescer: Coalescer,
        plan: ExecutionPlan,
        destination: str,
        body: Mapping[str, object],
    ) -> None:
        """Drive the egress pipeline under the no-splice gate + in-flight control (R7, R10).

        A per-request :class:`FirstByteLatch` gates retry/fallback: ``_sink`` sets it the instant
        the FIRST content byte is released (R7.2), and :func:`run_with_no_splice` requests a fresh
        attempt only while it is unset (R7.1/R7.3). Each attempt opens the injected
        ``ProviderClient`` for the routed destination and runs the shipped ``StreamPipeline``
        release loop; released frames are encoded by the one ``SSEEncoder``.

        **In-flight control (R10).** A per-request :class:`KillLatch` is the ``killed()`` seam; an
        :class:`InFlightControl` evaluated at each chunk boundary (:func:`_as_chunks`) flips it on a
        fired trigger and stops the source (R10.7). **Bounded timeouts (C27/R9).** A per-request
        :class:`StreamTimeouts` bounds the next-event await (R9.2) and the downstream write + idle
        (R9.3/R9.4), flipping the SAME latch on a breach. Either way :meth:`_finish_stream` renders
        the latch's posture code as the declared terminal ``Error_Frame`` at the ONE terminal site.

        On a clean finish the terminal ``[DONE]`` marker is emitted; a byte-less failure of every
        eligible attempt closes the stream without a splice (R7.4).
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
                offered,
                latch,
                _sink,
                coalescer,
                kill_latch,
                control,
                timeouts,
                plan,
                destination,
                body,
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
        plan: ExecutionPlan,
        destination: str,
        body: Mapping[str, object],
    ) -> StreamAttempt | None:
        """Yield the next eligible stream attempt, or ``None`` when none remains (R7.1).

        There is exactly ONE signed upstream to try (a signed fallback chain is a later card), so
        the first call returns an attempt and every later call returns ``None`` — the ``offered``
        one-way latch records that the sole provider response has been handed out, so a byte-less
        failure is NOT retried against the same provider forever and there is nothing further to
        splice. :func:`run_with_no_splice` only invokes this factory while the first-byte latch
        still permits a retry, so a fresh attempt is impossible once a byte is on the wire.

        The attempt builds the release loop via ``StreamPipeline.for_plan(plan, ...)`` — the
        resolved ``ExecutionPlan`` drives the enforcing-output set (R2.3), replacing the old
        pass-through flag — opens the injected ``ProviderClient`` to the plan-derived
        ``destination`` with the transformed ``body`` (replacing the old ``_DEFAULT_DESTINATION`` /
        ``_EMPTY_BODY`` stand-ins, R2.2 / R8.4), hands the per-request :class:`KillLatch` to
        ``pipeline.run`` as the ``killed()`` seam, and threads the :class:`InFlightControl` +
        :class:`StreamTimeouts` into :func:`_as_chunks` (every R10 trigger at each chunk boundary,
        R10.2-R10.7; the Inter_Chunk_Timeout on the next-event await, R9.2).
        """
        if offered.is_set():
            return None
        offered.set()

        async def _attempt() -> bool:
            try:
                pipeline = self._build_pipeline(plan)
            except EnforcementUndetermined:
                # Defensive: the proceed outcome carries a resolved plan, so `for_plan` resolves
                # the enforcing-output set without raising. Were the enforcement state ever
                # undetermined, fail CLOSED — flip the kill latch with the declared
                # ``plan_unavailable`` code and release no byte; `_finish_stream` renders it (R2.4).
                kill_latch.kill(PLAN_UNAVAILABLE)
                return latch.is_set()
            request = UpstreamRequest(destination=destination, body=body)
            chunks = _as_chunks(
                self.provider.open(request),
                coalescer,
                control,
                timeouts,
                error_scan=_ErrorFrameScan(
                    scanner=self.error_scanner,
                    resolver=self.resolver,
                    kill_latch=kill_latch,
                    executor=self.executor,
                ),
            )
            await pipeline.run(chunks, sink, kill_latch)
            # The sink set the first-byte latch the instant a content byte went out; report that
            # as the "released a byte" signal so run_with_no_splice closes the gate (R7.2/R7.4).
            return latch.is_set()

        return _attempt

    def _build_pipeline(self, plan: ExecutionPlan) -> StreamPipeline:
        """Construct the ``StreamPipeline`` with plan-derived output enforcement (R2.3).

        Builds the release loop via ``StreamPipeline.for_plan(plan, ...)``, which resolves the
        enforcing-output set ONCE before the first chunk from ``enforcing_output_rules(plan)`` (a
        non-empty set engages the holdback cap + byte ceiling; an empty set passes through) and
        raises :class:`EnforcementUndetermined` fail-closed on an unresolved plan — replacing the
        old hard-coded ``enforcing_output=False`` pass-through. The scanner / resolver / token
        counter are the injected seams, and the ``Window`` + holdback cap derive from the injected
        contract/config (no literal).
        """
        return StreamPipeline.for_plan(
            plan,
            scanner=self.scanner,
            resolver=self.resolver,
            token_index=TokenIndex(),
            cfg=self.cfg,
            metrics=HoldbackMetrics(),
            window=max_pattern_length(),
        )


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


def build_routes(chat: ChatRoute, misc: MiscRoute) -> tuple[RouteTable, RouteTable]:
    """Assemble the two route tables: the exact ``(method, path)`` table + the prefix table.

    GW16 wires the three misc surfaces live (R1.1): ``POST /v1/embeddings`` →
    :meth:`EmbeddingsSurface.handle`, ``GET /v1/models`` → :meth:`ModelsSurface.handle_list`,
    ``POST /v1/moderations`` → :meth:`ModerationsSurface.handle`, each built per-request over the
    injected :class:`MiscRoute` (``Surface(deps=misc)``, parity with ``ChatSurface(deps=self)``).
    The GW15 chat/completions routes are untouched and the responses/MCP/RAG/vector surfaces stay
    declared ``not_implemented`` placeholders (R1.3).

    ``GET /v1/models/{id}`` (retrieve) cannot be a static exact key, so a SECOND function-local
    ``prefix`` table binds ``("GET", "/v1/models/")`` → :meth:`ModelsSurface.handle_retrieve`
    (Decision 2); the matcher (``edge/app.py::_http``, task 8.1) consults it only AFTER an
    exact-match miss and the retrieve handler extracts the ``{id}`` from ``scope["path"]``.

    Both tables are built as function locals (returned as read-only ``Mapping`` values) so there is
    no module-level mutable table (R1.4). Returns ``(exact, prefix)``.
    """
    exact: RouteTable = {
        ("POST", "/v1/chat/completions"): chat.handle,
        ("POST", "/v1/completions"): chat.handle,
        ("POST", "/v1/responses"): _placeholder("responses"),
        ("POST", "/v1/embeddings"): EmbeddingsSurface(deps=misc).handle,
        ("GET", "/v1/models"): ModelsSurface(deps=misc).handle_list,
        ("POST", "/v1/moderations"): ModerationsSurface(deps=misc).handle,
        ("POST", "/v1/mcp"): _placeholder("mcp"),
        ("POST", "/v1/rag/query"): _placeholder("rag"),
        ("POST", "/v1/vector/query"): _placeholder("vector"),
    }
    prefix: RouteTable = {
        ("GET", "/v1/models/"): ModelsSurface(deps=misc).handle_retrieve,
    }
    return exact, prefix
