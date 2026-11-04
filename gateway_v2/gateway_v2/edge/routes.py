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

**The real chat handler (GW15).** :meth:`ChatRoute.handle` runs the §10.4 lifecycle through a
per-request :class:`~gateway_v2.edge.openai_chat.ChatSurface` before any provider is opened.
A streaming proceed uses the egress orchestration; a non-streaming proceed renders one OpenAI
JSON. Refusals render through :mod:`edge.errors`. The dependency surfaces live in
:mod:`gateway_v2.edge.chat_deps`.

**Placeholders (R2.3).** The MCP surface and the RAG/vector surface are *registered* here so the
route table is complete and a later card wires each one in place; until then each returns a
declared ``not_implemented`` value-code rendered by
the ``Error_Envelope`` — an HTTP ``501`` JSON body, NOT a raw ``404``-with-detail that would leak
internal routing shape.

**No capacity literal, no module-level mutable.** The route table is built inside
:func:`build_routes` (a function local, not a module global), so there is no module-level
mutable; the serving bounds (window, holdback cap, buffer high-water) all derive from the
injected ``ResourceContract`` / ``HoldbackConfig`` and never from a literal here.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from gateway_v2.audit.record import AuditRecord
from gateway_v2.dispatch.provider import ProviderClient
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain.plan import ExecutionPlan, Surface
from gateway_v2.domain.posture import STREAM_KILLED
from gateway_v2.edge import errors
from gateway_v2.edge.asgi_sse import (
    ASGIReceive,
    ASGIScope,
    ASGISend,
)
from gateway_v2.edge.asgi_sse import TerminalLatch as _TerminalLatch
from gateway_v2.edge.asgi_sse import read_body as _read_body
from gateway_v2.edge.audit_emit import emit_chat_audit
from gateway_v2.edge.cancel import InFlightControlSource, KillLatch
from gateway_v2.edge.chat_deps import (
    AdmissionDecider,
    BudgetDecider,
    InputDetector,
    fail_closed_admission,
    fail_closed_budget,
    no_findings_detector,
    unresolved_snapshot_source,
)
from gateway_v2.edge.chat_stream import ChatStream, _ProducedModeHolder, finish_stream
from gateway_v2.edge.console_projection import (
    apply_projection,
    console_from_holds,
)
from gateway_v2.edge.disconnect import StreamingDone, run_until_disconnect
from gateway_v2.edge.error_frame_scan import ErrorFrameScanner
from gateway_v2.edge.executor import ScanExecutor
from gateway_v2.edge.identity_resolver import (
    IdentityResolver,
    fail_closed_resolver,
    refusal_request_id,
)
from gateway_v2.edge.mcp_deps import McpDeps
from gateway_v2.edge.openai_misc import (
    EmbeddingsSurface,
    MiscRoute,
    ModerationsSurface,
)
from gateway_v2.edge.openai_misc_models import ModelsSurface
from gateway_v2.edge.outcome_metrics import OutcomeObserver
from gateway_v2.edge.phase_resolvers import PhaseResolverFactory
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.responses_store import ResponseStore
from gateway_v2.edge.stream_response import StreamResponse
from gateway_v2.edge.wire.openai import (
    CanonicalRequest,
    OpenAIRequestParser,
    OpenAIResponseRenderer,
)
from gateway_v2.edge.wire.sse import DEFAULT_MODEL, DEFAULT_STREAM_ID, SSEEncoder, StreamIdentity
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.egress.output_guard import OutputResolver
from gateway_v2.egress.stream import (
    ScanProtocol,
)
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.runtime.holdback_config import HoldbackConfig
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.resources_tools import ToolBounds
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


async def _emit_error_code(
    send: ASGISend, code: str, request_id: str | None = None,
    param: str | None = None, boundary: int | None = None,
    console: dict[str, object] | None = None,
) -> None:
    """Render a value-code through the single ``Error_Envelope`` and emit it as HTTP.

    Every value-code that reaches a non-streaming handler passes through
    :func:`errors.render_http`, so an unmapped code renders the declared generic error rather
    than leaking raw internal detail (R3.4). The streaming chat handler uses the SSE rendering
    instead (``errors.render_sse``), so the two sides never disagree on the error shape.

    ``request_id`` is the gateway-minted per-request id (``None`` at a pre-identity site); it
    threads into the HTTP body + the ``x-request-id`` header. ``param`` names the offending field
    for a boundary parse refusal (``None`` otherwise). ``boundary`` is the permitted-maximum VALUE
    a contract-bearing caller supplies for the ``invalid_max_*`` codes (``None`` otherwise);
    ``edge/errors`` formats it into the boundary-free declared message — the capacity number is
    sourced from ``max_output_tokens_boundary()`` at the caller, never a literal here or there.
    """
    response = errors.render_http(code, param=param, request_id=request_id, boundary=boundary)
    await emit_response(send, apply_projection(response, console))


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
    #: The streaming completed-match source handed to the holdback pipeline (the egress
    #: ``Detector`` seam, task 9.1). ``None`` keeps the shipped thin harness (no completed-match
    #: findings on the streaming path); a boot that wires the GW07/GW08/GW09 detector passes it
    #: here, above ``detect``, so ``egress`` still never names ``detect``.
    output_detector: ErrorFrameScanner | None = None
    #: OPTIONAL phase-aware resolver factory (Group E, Property 11): when set, input and output
    #: resolve through ``resolver_for`` bound to the pinned plan and the phase. ``None`` keeps the
    #: single ``resolver`` byte-identical (Preservation 3.5 / 3.13).
    phase_resolvers: PhaseResolverFactory | None = None
    #: Bounded audit enqueue. ``None`` leaves the response unchanged and writes no row.
    audit: Callable[[AuditRecord], bool] | None = None
    #: The ``/metrics`` request counters (tenant, rejection, posture). ``None`` counts nothing.
    observer: OutcomeObserver | None = None
    #: ONE holdback producer for every stream, so ``/metrics`` can read what the streams recorded.
    holdback_metrics: HoldbackMetrics = HoldbackMetrics()
    #: GW26. A set phase factory enforces only rules that name this surface.
    surface: Surface = Surface.CHAT
    #: GW34. The tool bounds, resolved once at boot; ``None`` is a tools-disabled route.
    tool_bounds: ToolBounds | None = None
    #: GW34. The epoch clock stamped into a stream's ``created`` (``clock`` is monotonic).
    wall_clock_s: Callable[[], float] = time.time

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

        ``ChatSurface`` is imported inside the method because ``edge/openai_chat.py`` imports
        from THIS module at module level — a top-level import of it here would cycle.
        """
        from gateway_v2.edge.openai_chat import ChatSurface

        holds = RequestHolds()
        outcome = None
        killed = False  # the client left before a streaming response finished (GW34 R6)
        try:
            body = await _read_body(receive)
            req, outcome = await ChatSurface(deps=self).run_lifecycle(scope, body, holds)
            if outcome.refuse_code is not None:
                await self._render_refusal(
                    send, req, outcome.refuse_code, outcome.request_id,
                    outcome.refuse_param, holds,
                )
                return
            # Proceed: the lifecycle guarantees all three provider inputs are set on a non-refusal.
            plan = outcome.plan
            destination = outcome.destination
            upstream_body = outcome.body
            assert plan is not None and destination is not None and upstream_body is not None
            assert req is not None  # parse succeeded on any proceed outcome
            if req.streaming:
                async def _serve(done: StreamingDone) -> None:
                    await self._stream_response(
                        send, plan, destination, upstream_body, holds, req, done=done
                    )

                killed = await run_until_disconnect(receive, _serve)
                return
            await ChatSurface(deps=self).collect_and_render(
                req, plan, destination, upstream_body, send, holds
            )
        finally:
            refused = None if outcome is None else outcome.refuse_code
            if killed:
                refused = STREAM_KILLED
            emit_chat_audit(
                self.audit, holds, refused, observer=self.observer, detail=_tool_call_detail(holds)
            )
            holds.release(self.admission, self.quota)

    async def _render_refusal(
        self,
        send: ASGISend,
        req: CanonicalRequest | None,
        code: str,
        request_id: str | None,
        param: str | None = None,
        holds: RequestHolds | None = None,
    ) -> None:
        """Render a §10.4 refusal through the single ``Error_Envelope`` (R12.1).

        EVERY refusal renders as an HTTP body, whether or not the request asked to stream (GW34
        R7): a refused stream is a plain HTTP error with the same bytes as the non-stream refusal,
        so the SDK raises its status-based error class and applies its retry policy. The one
        ``edge/errors`` envelope maps the value-code, so an unmapped code still renders a declared
        generic error rather than a raw framework 500.

        ``request_id`` is the gateway-minted per-request id when the refusing phase ran after
        identity resolution (``None`` for a pre-identity refusal); it is threaded into the HTTP
        ``Error_Envelope`` body + the ``x-request-id`` response header so the render echoes the id
        the admission / budget verdicts carry.

        ``param`` names the offending request field for a boundary parse refusal (``max_tokens`` /
        ``max_completion_tokens``, ``None`` otherwise), echoed as ``error.param`` (Group F). The
        permitted-maximum ``boundary`` VALUE from ``self.contract`` is passed on every HTTP refusal;
        ``edge/errors`` uses it ONLY for the ``invalid_max_*`` codes (whose declared message is
        boundary-free, the boundary being a capacity position), so the real boundary reaches the
        HTTP body while the number lives only in ``runtime/resources.py``.
        """
        if request_id is None and (minted := refusal_request_id(code)) is not None:
            # GW36 S3 (N11): identity's refusal (the only post-parse one with no id yet) is plain
            # HTTP whatever `stream` asked, as OpenAI's 401 is, with a random id.
            request_id, req = minted, None
        del req  # GW34: every post-parse refusal is a plain HTTP error, stream or not (R7)
        await _emit_error_code(
            send, code, request_id, param, self.contract.max_output_tokens_boundary(),
            None if holds is None else console_from_holds(
                holds, output_action=None, provider_called=False,
            ),
        )

    async def _stream_response(
        self,
        send: ASGISend,
        plan: ExecutionPlan,
        destination: str,
        body: Mapping[str, object],
        holds: RequestHolds | None = None,
        req: CanonicalRequest | None = None,
        *,
        plain_pre_byte: bool = True,
        done: StreamingDone | None = None,
    ) -> None:
        """Serve a streaming proceed outcome through the egress orchestration (R2.2, R7, R10).

        Reached only on a proceed outcome for a streaming request. The coalescer admit fails
        CLOSED when the derived per-stream high-water is below one byte
        (``STREAM_BUFFER_UNAVAILABLE``). A per-request ``SSEEncoder`` (one stable ``id`` / ``model``
        / ``created`` from ``req``) is the sink for the released frames, which flow through the
        shipped pipeline built via ``select_pipeline(plan, ...)`` and the tool-call assembler.

        **Deferred start (GW34 R7).** The SSE response starts at the first content-bearing frame,
        so a coalescer refusal or any failure before it is a plain HTTP error with the exact
        non-stream bytes. ``plain_pre_byte=False`` (the Responses adapter, which translates our
        SSE bytes itself) keeps today's eager start and SSE ``Error_Frame`` behaviour.

        ``holds`` is the per-request ledger threaded from :meth:`handle` so the streaming provider
        open records ``mark_dispatched`` (Group D); it defaults to a fresh per-call ledger so a
        shipped caller that drives this method directly stays callable.
        """
        if holds is None:
            holds = RequestHolds()

        async def emit_error(code: str) -> None:
            dispatched = holds.dispatched
            await _emit_error_code(
                send, code, holds.request_id or None, None,
                self.contract.max_output_tokens_boundary(),
                console_from_holds(
                    holds, output_action="block" if dispatched else None,
                    provider_called=dispatched,
                ),
            )

        response = StreamResponse(
            send, emit_error=emit_error, request_id=holds.request_id or None
        )
        coalescer = Coalescer(contract=self.contract, active_streams=self.active_streams)
        verdict = coalescer.admit()
        identity = StreamIdentity(
            stream_id=f"chatcmpl-{holds.request_id}" if holds.request_id else DEFAULT_STREAM_ID,
            model=DEFAULT_MODEL if req is None else req.model,
            created=int(self.wall_clock_s()),
        )
        encoder = SSEEncoder(identity=identity)
        if verdict.code is not None:
            if plain_pre_byte:
                await response.fail_pre_byte(verdict.code)
                return
            await response.start()
            await response.write_started(errors.render_sse(verdict.code))
            await response.close()
            return
        if not plain_pre_byte:
            await response.start()
        await ChatStream(self).stream(
            response, encoder, coalescer, plan, destination, body, holds, req, done
        )

    async def _finish_stream(
        self,
        send: ASGISend,
        encoder: SSEEncoder,
        kill_latch: KillLatch,
        sent_terminal: _TerminalLatch,
        produced: _ProducedModeHolder,
    ) -> None:
        """Delegate to :func:`finish_stream`, the ONE terminal site (does not read ``self``)."""
        await finish_stream(send, encoder, kill_latch, sent_terminal, produced)


def _tool_call_detail(holds: RequestHolds) -> Mapping[str, str] | None:
    """Audit detail for a request that carried tool calls: COUNTS as digit strings, nothing else
    (no tool name, id, argument or schema text reaches an audit field, R11)."""
    released, withheld = holds.tool_calls_released, holds.tool_calls_withheld
    if not (released or withheld):
        return None
    return {"tool_calls_released": str(released), "tool_calls_withheld": str(withheld)}


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


def build_routes(
    chat: ChatRoute, misc: MiscRoute, response_store: ResponseStore | None = None,
    mcp: McpDeps | None = None,
) -> tuple[RouteTable, RouteTable]:
    """Assemble the two route tables: the exact ``(method, path)`` table + the prefix table.

    GW16 wires embeddings, models, and moderations over :class:`MiscRoute`. GW15 wires
    ``/v1/responses`` over ``chat`` (memory store when ``None``). GW17 wires ``/v1/mcp`` when
    ``mcp`` is given, else it stays ``501``; RAG/vector stay ``501`` (R1.3).

    ``GET /v1/models/{id}`` (retrieve) cannot be a static exact key, so a SECOND function-local
    ``prefix`` table binds ``("GET", "/v1/models/")`` → :meth:`ModelsSurface.handle_retrieve`
    (Decision 2); the matcher (``edge/app.py::_http``, task 8.1) consults it only AFTER an
    exact-match miss and the retrieve handler extracts the ``{id}`` from ``scope["path"]``.

    Both tables are built as function locals (returned as read-only ``Mapping`` values) so there is
    no module-level mutable table (R1.4). Returns ``(exact, prefix)``.
    """
    from gateway_v2.edge.surface_entries import surface_entries

    responses_exact, responses_prefix = surface_entries(chat, response_store, mcp)
    exact: RouteTable = {
        ("POST", "/v1/chat/completions"): chat.handle,
        ("POST", "/v1/completions"): chat.handle,
        **responses_exact,
        ("POST", "/v1/embeddings"): EmbeddingsSurface(deps=misc).handle,
        ("GET", "/v1/models"): ModelsSurface(deps=misc).handle_list,
        ("POST", "/v1/moderations"): ModerationsSurface(deps=misc).handle,
        ("POST", "/v1/rag/query"): _placeholder("rag"),
        ("POST", "/v1/vector/query"): _placeholder("vector"),
    }
    prefix: RouteTable = {
        ("GET", "/v1/models/"): ModelsSurface(deps=misc).handle_retrieve,
        **responses_prefix,
    }
    return exact, prefix
