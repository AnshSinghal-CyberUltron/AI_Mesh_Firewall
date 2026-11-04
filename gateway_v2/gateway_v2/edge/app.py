"""ASGI app assembly: router, lifespan, ``/metrics``, ``/readyz`` (GW12, task 7.2).

``build_app`` assembles the serving skin as a MINIMAL raw-ASGI 3.0 application — a single
``async def app(scope, receive, send)`` callable — because the repo pulls in NO HTTP framework
(``pyproject.toml`` deps are ``psycopg`` + ``redis`` only; adding starlette/fastapi would take a
dependency the package deliberately avoids). Every HTTP reply is a framework-free
:class:`gateway_v2.edge.errors.HTTPResponse` value emitted onto the ASGI ``send`` channel via the
``emit_response`` helper (``http.response.start`` + ``http.response.body``).

What this assembles (R2.1):

* a **router** — the ``(method, path) -> handler`` table built by
  :func:`gateway_v2.edge.routes.build_routes`, with the streaming chat route wired live and the
  responses/misc/mcp/rag surfaces (an unwired one is a declared placeholder, R2.2/R2.3);
* a **lifespan** handler — drains the ASGI ``lifespan`` protocol (``startup`` / ``shutdown``) so
  a conforming server can boot and stop the app cleanly;
* **``/readyz``** — returns ``200`` when every required state dependency is available and a
  non-success ``503`` NAMING the unavailable dependency when one is missing (R2.4/R2.5), wired to
  the ``runtime.state_stamp.state_ready(view)`` contract the LGW05b handoff pins (``/readyz`` must
  call ``state_ready(view)`` and return 503 with its reason when not ready); and
* **``/metrics``** — the Prometheus text exposition computed OFF the serving loop (R2.6 / C38
  R13.2): the handler offloads the CPU-bound snapshot via ``asyncio.to_thread``.

C38 serving-loop discipline (R13.1): ``build_app`` constructs the one
:class:`~gateway_v2.edge.executor.ScanExecutor` — a GIL-releasing thread pool sized by
``contract.pool_size(PoolKind.SCANNER)`` — and injects it into the chat route so the CPU-bound
error-frame scan runs off the forwarding loop; the pool is released on ``lifespan.shutdown``.

``edge`` is the top layer. The scanner and the ``ProviderClient`` are injected into
:func:`build_app` above ``detect`` (``egress`` must never import ``detect``, R14.1/R14.2).

**Split.** The route table and handlers live in :mod:`gateway_v2.edge.routes`; this module owns
the ASGI protocol loop + the two operational endpoints. **No capacity literal, no module-level
mutable:** the route table is a function local and the serving bounds derive from the contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from gateway_v2.admit.org_limits import OrgLimits
from gateway_v2.audit.record import AuditRecord
from gateway_v2.dispatch.provider import ProviderClient
from gateway_v2.dispatch.routed_provider import RoutedProvider
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain.incident import (
    IncidentDecider,
    fail_closed_gate,
    fail_closed_org,
    fail_closed_recheck,
)
from gateway_v2.edge import errors
from gateway_v2.edge.app_defaults import no_error_findings, one_stream
from gateway_v2.edge.cancel import InFlightControlSource
from gateway_v2.edge.chat_deps import (
    AdmissionDecider,
    BudgetDecider,
    InputDetector,
    OrgLimitDecider,
    fail_closed_admission,
    fail_closed_budget,
    no_findings_detector,
    resolve_tool_bounds,
    unresolved_snapshot_source,
)
from gateway_v2.edge.error_frame_scan import ErrorFrameScanner
from gateway_v2.edge.executor import ScanExecutor
from gateway_v2.edge.identity_resolver import (
    IdentityResolver,
    TenantIdentity,
    fail_closed_resolver,
)
from gateway_v2.edge.incident_recheck import Recheck
from gateway_v2.edge.inflight_source import InFlightControlFactory, RefusingControlSource
from gateway_v2.edge.mcp_deps import McpDeps
from gateway_v2.edge.openai_misc import MiscRoute
from gateway_v2.edge.outcome_metrics import OutcomeObserver
from gateway_v2.edge.phase_resolvers import PhaseResolverFactory
from gateway_v2.edge.rag_deps import RagDeps
from gateway_v2.edge.readiness import (
    METRICS_DIRECTORY_REASON,
    DirectoryGate,
    make_metrics_readiness,
)
from gateway_v2.edge.responses_store import ResponseStore
from gateway_v2.edge.routes import (
    ASGIReceive,
    ASGIScope,
    ASGISend,
    ChatRoute,
    RouteHandler,
    RouteTable,
    build_routes,
    emit_response,
)
from gateway_v2.edge.unauth_audit import UnauthAuditGate
from gateway_v2.edge.wire.openai import OpenAIRequestParser, OpenAIResponseRenderer
from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer
from gateway_v2.egress.output_guard import OutputResolver
from gateway_v2.egress.stream import ScanProtocol
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.runtime.holdback_config import HoldbackConfig, load_holdback_config
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.state_stamp import StampView
from gateway_v2.runtime.stream_metrics import PerRequestExports

__all__ = (
    "ASGIApplication",
    "CatalogModel",
    "CatalogSource",
    "MetricsDirectorySource",
    "MetricsRegistry",
    "ReadinessProbe",
    "app",
    "build_app",
    "empty_catalog_source",
)

# --------------------------------------------------------------------------- #
# Injected surfaces that have no concrete type in the package yet.
# --------------------------------------------------------------------------- #


@runtime_checkable
class MetricsRegistry(Protocol):
    """What ``/metrics`` needs from the metrics source: an off-loop text exposition (R2.6).

    ``exposition()`` returns the Prometheus text body as a ``str``. It is called inside
    ``asyncio.to_thread`` so the (O(samples), CPU-bound) snapshot+format runs OFF the serving
    loop (C38 R13.2). A registry is read-only here — the serving skin never mutates it.
    """

    def exposition(self) -> str: ...


@runtime_checkable
class MetricsDirectorySource(Protocol):
    """A :class:`MetricsRegistry` that ALSO backs the ``/readyz`` metrics-directory gate (R10).

    GW14d's concrete registry — :class:`gateway_v2.edge.publisher.Publisher` — owns a bounded
    Cardinality_Directory whose availability is the metrics half of ``/readyz`` (Requirement 10).
    A registry that exposes ``directory() -> DirectoryGate`` lets :func:`build_app` compose the
    metrics-directory gate (via :func:`gateway_v2.edge.readiness.make_metrics_readiness`) ON TOP OF
    the state-dependency gate, gating state first then the directory. A plain registry (only
    ``exposition()``) does NOT satisfy this protocol, so the composition is skipped and the existing
    ``readiness`` behaviour is left exactly as it was — the backward-compatible fallback.
    """

    def exposition(self) -> str: ...

    def directory(self) -> DirectoryGate: ...


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """One entry of the Allowed_Model_Catalog in the OpenAI ``model`` shape (R7.1/R7.2).

    The org/plan-derived model the ``GET /v1/models`` list and ``GET /v1/models/{id}`` retrieve
    surfaces render (GW16). ``id`` is the model id the stock SDK's ``models.retrieve(id)`` names;
    ``created`` is the Unix-second creation stamp the OpenAI shape carries; ``owned_by`` is the
    owning org/vendor. Frozen + slotted: an immutable value built by the catalog backend, never
    mutated by a surface. ``created`` is an epoch-second timestamp the backend supplies, not a
    capacity-position bound.
    """

    id: str
    created: int
    owned_by: str


@runtime_checkable
class CatalogSource(Protocol):
    """The injected Allowed_Model_Catalog source, resolved against Tenant_Identity (R7.4).

    The seam the GW16 Models_Surface reads its allowed-model catalog from, mirroring the shipped
    injected-dependency discipline (the detector / resolver / provider are injected the same way).
    ``catalog_for`` returns the ordered tuple of :class:`CatalogModel` the request's identity may
    see; the surface renders it as the OpenAI list / retrieve shape and renders ``model_not_found``
    for an id absent from the tuple. The real org/plan-derived backend is a DEFERRED seam (R11.3);
    :data:`empty_catalog_source` is the honest-empty default until it is wired.
    """

    def catalog_for(self, identity: TenantIdentity) -> tuple[CatalogModel, ...]: ...


class _EmptyCatalogSource:
    """A :class:`CatalogSource` that returns no models — the honest-empty default.

    The default ``MiscRoute.catalog`` for a deployment / test that has not yet wired the real
    org/plan-derived catalog backend (R7.4/R11.3). It returns an EMPTY catalog for every identity,
    so ``GET /v1/models`` lists nothing and every ``GET /v1/models/{id}`` retrieve resolves to
    ``model_not_found`` — honest rather than fabricating a model that does not exist. ``__slots__``
    so there is no instance state; a single shared instance (:data:`empty_catalog_source`) is safe
    to reuse.
    """

    __slots__ = ()

    def catalog_for(self, identity: TenantIdentity) -> tuple[CatalogModel, ...]:
        """Return the empty catalog for any identity (honest-empty default)."""
        del identity
        return ()


#: The shared honest-empty default catalog source. An immutable, stateless instance (not a
#: module-level mutable), used as the ``MiscRoute.catalog`` default until the real org/plan-derived
#: backend is wired at boot (GW16 task 8.1). Every retrieve against it is a ``model_not_found``.
empty_catalog_source: CatalogSource = _EmptyCatalogSource()


#: The readiness probe ``/readyz`` consults (R2.4/R2.5). ``(ready, reason)``: ``reason`` is
#: ``None`` when ready and NAMES the unavailable dependency otherwise — exactly the shape
#: ``runtime.state_stamp.state_ready`` returns, so a deployment can hand that function (bound to
#: its ``StampView``) straight in. ``None`` is accepted for an unwired deployment and reports
#: ready, matching ``state_ready(None) == (True, None)``.
ReadinessProbe = Callable[[], tuple[bool, str | None]]

#: An ASGI 3.0 application: the ``async def app(scope, receive, send)`` coroutine function a
#: conforming server invokes once per connection (and once for the ``lifespan`` protocol).
ASGIApplication = Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]


# --------------------------------------------------------------------------- #
# build_app (R2.1).
# --------------------------------------------------------------------------- #


def build_app(
    *,
    contract: ResourceContract,
    provider: ProviderClient,
    scanner: ScanProtocol,
    resolver: OutputResolver,
    metrics_registry: MetricsRegistry,
    clock: Callable[[], float],
    readiness: ReadinessProbe | None = None,
    state_view: StampView | None = None,
    active_streams: Callable[[], int] | None = None,
    control: InFlightControlSource | None = None,
    error_scanner: ErrorFrameScanner | None = None,
    input_detector: InputDetector | None = None,
    identity: IdentityResolver | None = None,
    snapshot_source: Callable[[], ReplicaSnapshot] | None = None,
    admission: AdmissionDecider | None = None,
    quota: BudgetDecider | None = None,
    exports: PerRequestExports | None = None,
    catalog: CatalogSource | None = None,
    output_detector: ErrorFrameScanner | None = None,
    phase_resolvers: PhaseResolverFactory | None = None,
    audit: Callable[[AuditRecord], bool] | None = None,
    response_store: ResponseStore | None = None,
    mcp: McpDeps | None = None,
    rag: RagDeps | None = None,
    observer: OutcomeObserver | None = None,
    holdback_metrics: HoldbackMetrics | None = None,
    org_limits: OrgLimitDecider | None = None,
    routing: RoutedProvider | None = None,
    control_factory: InFlightControlFactory | None = None,
    incident_gate: IncidentDecider | None = None,
    incident_recheck: Recheck | None = None,
    unauth_audit: UnauthAuditGate | None = None,
) -> ASGIApplication:
    """Assemble the raw-ASGI serving skin (R2.1).

    Wires the chat, responses, and misc routes plus the mcp/rag placeholders, and mounts
    ``/readyz`` + ``/metrics``. The holdback cap is loaded once at boot (``load_holdback_config``);
    the window and per-stream buffer high-water derive from ``contract`` (no literal).
    ``readiness`` defaults to an always-ready probe; ``active_streams`` to a single-stream count
    until GW19. ``control`` is the in-flight source polled per chunk (R10); GW29 defaults it to
    :class:`RefusingControlSource` (fail closed: an unwired stream is cut, never left uncontrolled),
    and ``control_factory`` / ``incident_gate`` / ``incident_recheck`` (all unwired = refuse) take
    the per-stream kill-switch source and the phase 4b / attempt gates (dev: pass-through).
    ``error_scanner`` is the mid-stream error-frame detector (R11, default: no findings).
    **Group E.** ``phase_resolvers`` binds each route's resolver to the plan and phase. **GW14d.**
    ``/readyz`` gates state, then the metrics directory; ``observer`` counts.
    **GW16.** One :class:`MiscRoute` via :func:`_build_misc_route`; ``catalog`` feeds Models;
    ``response_store`` backs ``/v1/responses``; ``mcp`` wires ``/v1/mcp``; **GW31** ``rag`` wires
    RAG + vector. **GW15 §10.4.** One :class:`ChatRoute` via :func:`_build_chat_route`. **GW27.**
    ``org_limits`` is the org rate / burst / TPM decider. ``build_app`` stays pure assembly (no
    store read, no task spawned); bounds derive from the injected ``ResourceContract`` /
    ``HoldbackConfig`` with no capacity literal. Returns the ``app(scope, receive, send)`` callable.
    """
    cfg, _cfg_logs = load_holdback_config()
    # C38 (R13.1): the scan executor, sized by `pool_size(PoolKind.SCANNER)`; shared by all streams.
    scan_executor = ScanExecutor(contract=contract)
    limits = org_limits if org_limits is not None else OrgLimits(contract)
    chat = _build_chat_route(
        org_limits=limits,
        contract=contract,
        provider=provider,
        scanner=scanner,
        resolver=resolver,
        cfg=cfg,
        clock=clock,
        scan_executor=scan_executor,
        active_streams=active_streams,
        control=control,
        error_scanner=error_scanner,
        input_detector=input_detector,
        identity=identity,
        snapshot_source=snapshot_source,
        admission=admission,
        quota=quota,
        exports=exports,
        output_detector=output_detector,
        phase_resolvers=phase_resolvers,
        audit=audit,
        observer=observer,
        holdback_metrics=holdback_metrics,
        routing=routing,
        control_factory=control_factory,
        incident_gate=incident_gate,
        incident_recheck=incident_recheck,
        unauth_audit=unauth_audit,
    )
    misc = _build_misc_route(
        org_limits=limits,
        contract=contract,
        provider=provider,
        resolver=resolver,
        clock=clock,
        input_detector=input_detector,
        identity=identity,
        snapshot_source=snapshot_source,
        admission=admission,
        quota=quota,
        exports=exports,
        catalog=catalog,
        phase_resolvers=phase_resolvers,
        observer=observer,
        audit=audit,
        incident_gate=incident_gate,
        incident_recheck=incident_recheck,
    )
    exact, prefix = build_routes(chat, misc, response_store, mcp, rag)
    probe = _resolve_readiness(
        readiness=readiness, metrics_registry=metrics_registry, state_view=state_view
    )
    skin = _ServingSkin(
        routes=exact,
        prefix_routes=prefix,
        metrics_registry=metrics_registry,
        readiness=probe,
        clock=clock,
        executor=scan_executor,
    )
    return skin.asgi


def _build_chat_route(
    *,
    org_limits: OrgLimitDecider,
    contract: ResourceContract,
    provider: ProviderClient,
    scanner: ScanProtocol,
    resolver: OutputResolver,
    cfg: HoldbackConfig,
    clock: Callable[[], float],
    scan_executor: ScanExecutor,
    active_streams: Callable[[], int] | None,
    control: InFlightControlSource | None,
    error_scanner: ErrorFrameScanner | None,
    input_detector: InputDetector | None,
    identity: IdentityResolver | None,
    snapshot_source: Callable[[], ReplicaSnapshot] | None,
    admission: AdmissionDecider | None,
    quota: BudgetDecider | None,
    exports: PerRequestExports | None,
    output_detector: ErrorFrameScanner | None = None,
    phase_resolvers: PhaseResolverFactory | None = None,
    audit: Callable[[AuditRecord], bool] | None = None,
    observer: OutcomeObserver | None = None,
    holdback_metrics: HoldbackMetrics | None = None,
    routing: RoutedProvider | None = None,
    control_factory: InFlightControlFactory | None = None,
    incident_gate: IncidentDecider | None = None,
    incident_recheck: Recheck | None = None,
    unauth_audit: UnauthAuditGate | None = None,
) -> ChatRoute:
    """Construct the single chat route. Unwired deps use their fail-closed defaults.

    ``audit`` defaults to ``None``: the response is unchanged and no record is enqueued.
    """
    request_exports = exports if exports is not None else PerRequestExports()
    return ChatRoute(
        contract=contract,
        provider=provider,
        scanner=scanner,
        resolver=resolver,
        cfg=cfg,
        router=DispatchRouter(),
        active_streams=active_streams if active_streams is not None else one_stream,
        control=control if control is not None else RefusingControlSource(),
        clock=clock,
        error_scanner=error_scanner if error_scanner is not None else no_error_findings,
        executor=scan_executor,
        org_limits=org_limits,
        # --- GW15 §10.4 lifecycle deps, constructed once at boot (task 6.1) ---
        parser=OpenAIRequestParser(),
        renderer=OpenAIResponseRenderer(),
        transform=Transform(),
        input_detector=input_detector if input_detector is not None else no_findings_detector,
        identity=identity if identity is not None else fail_closed_resolver,
        snapshot_source=(
            snapshot_source if snapshot_source is not None else unresolved_snapshot_source
        ),
        admission=admission if admission is not None else fail_closed_admission,
        quota=quota if quota is not None else fail_closed_budget,
        exports=request_exports,
        output_detector=output_detector,
        phase_resolvers=phase_resolvers,
        audit=audit,
        observer=observer,
        holdback_metrics=holdback_metrics if holdback_metrics is not None else HoldbackMetrics(),
        routing=routing,
        control_factory=control_factory,
        incident_gate=incident_gate if incident_gate is not None else fail_closed_gate,
        incident_recheck=(
            incident_recheck if incident_recheck is not None else fail_closed_recheck
        ),
        tool_bounds=resolve_tool_bounds(contract),
        unauth_audit=unauth_audit,
    )


def _build_misc_route(
    *,
    org_limits: OrgLimitDecider,
    contract: ResourceContract,
    provider: ProviderClient,
    resolver: OutputResolver,
    clock: Callable[[], float],
    input_detector: InputDetector | None,
    identity: IdentityResolver | None,
    snapshot_source: Callable[[], ReplicaSnapshot] | None,
    admission: AdmissionDecider | None,
    quota: BudgetDecider | None,
    exports: PerRequestExports | None,
    catalog: CatalogSource | None,
    phase_resolvers: PhaseResolverFactory | None = None,
    observer: OutcomeObserver | None = None,
    audit: Callable[[AuditRecord], bool] | None = None,
    incident_gate: IncidentDecider | None = None,
    incident_recheck: Recheck | None = None,
) -> MiscRoute:
    """Construct the single :class:`MiscRoute` for the three GW16 misc surfaces (task 7.1/8.1).

    Built ONCE at boot the same way :func:`_build_chat_route` builds the chat route, and shared
    across requests (the frozen route holds nothing request-scoped, so two-tenant isolation holds).
    The ``input_detector`` (``detect``) / ``resolver`` (``egress``) / ``provider`` (``dispatch``) /
    ``catalog`` (``edge/app.py``) are injected HERE in ``edge`` — above ``detect`` — so ``egress``
    never imports ``detect`` (R4.5). Each unwired security dep falls back to its shipped
    fail-closed default (identity refuses, admission sheds, budget refuses, the snapshot resolves
    ``PlanUnknownTenant``); the injected ``catalog`` is threaded through (``None`` ⇒ the
    honest-empty :data:`empty_catalog_source`, every retrieve ``model_not_found``) and ``renderer``
    defaults to the stateless :class:`~gateway_v2.edge.wire.openai_misc.MiscResponseRenderer`.
    The ``contract`` is the same injected :class:`ResourceContract` the chat route carries; the
    embeddings budget phase charges its Group F token estimate through it.
    """
    request_exports = exports if exports is not None else PerRequestExports()
    return MiscRoute(
        org_limits=org_limits,
        contract=contract,
        provider=provider,
        clock=clock,
        renderer=MiscResponseRenderer(),
        catalog=catalog if catalog is not None else empty_catalog_source,
        identity=identity if identity is not None else fail_closed_resolver,
        admission=admission if admission is not None else fail_closed_admission,
        quota=quota if quota is not None else fail_closed_budget,
        snapshot_source=(
            snapshot_source if snapshot_source is not None else unresolved_snapshot_source
        ),
        resolver=resolver,
        input_detector=input_detector if input_detector is not None else no_findings_detector,
        exports=request_exports,
        phase_resolvers=phase_resolvers,
        observer=observer,
        audit=audit,
        incident_gate=incident_gate if incident_gate is not None else fail_closed_gate,
        incident_org_gate=(
            incident_gate.decide_org if incident_gate is not None else fail_closed_org
        ),
        incident_recheck=incident_recheck if incident_recheck is not None else fail_closed_recheck,
    )


# --------------------------------------------------------------------------- #
# The serving skin: the ASGI protocol loop + the two operational endpoints.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _ServingSkin:
    """Holds the assembled routes + operational wiring; exposes the ASGI callable.

    Frozen + slotted: assembled once at boot and shared across every connection. All per-request
    state lives on the ASGI ``scope``/channels, never on the skin.
    """

    routes: RouteTable
    #: The longest-prefix ``(method, prefix) -> handler`` table (Decision 2). Built function-local
    #: in :func:`build_routes` and threaded on here the same way ``routes`` is. GW16 task 7.1 holds
    #: it; the ``_http`` prefix-match step that consults it for ``GET /v1/models/{id}`` is wired in
    #: task 8.1.
    prefix_routes: RouteTable
    metrics_registry: MetricsRegistry
    readiness: ReadinessProbe
    clock: Callable[[], float]
    executor: ScanExecutor

    async def asgi(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """The ASGI 3.0 entry point: dispatch by ``scope["type"]``.

        ``lifespan`` is drained by :meth:`_lifespan`; an ``http`` connection is routed by
        ``(method, path)``. A websocket or any other scope type is declined cleanly (the serving
        skin speaks HTTP + lifespan only).
        """
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope_type == "http":
            await self._http(scope, receive, send)
            return
        # Any other scope (e.g. websocket) is not served here; close without a protocol error.

    async def _lifespan(self, receive: ASGIReceive, send: ASGISend) -> None:
        """Drain the ASGI ``lifespan`` protocol so a server can boot/stop the app.

        Answers ``lifespan.startup`` with ``lifespan.startup.complete`` and
        ``lifespan.shutdown`` with ``lifespan.shutdown.complete``. The serving skin holds no
        store connection of its own to open or close (the state loop + provider pools are owned
        elsewhere), so the handler is a clean acknowledgement loop.
        """
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message_type == "lifespan.shutdown":
                # Release the C38 scan executor's worker pool on a clean shutdown (R13.1).
                self.executor.shutdown()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _http(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """Route one HTTP connection by ``(method, path)``.

        ``/readyz`` and ``/metrics`` are the two operational endpoints handled inline; every
        other path is looked up in the route table. A miss renders through the single
        ``Error_Envelope``: 405 ``method_not_allowed`` (with ``allow``) when the path is served
        under another method, else 404 ``endpoint_not_found`` — never a 5xx, which the OpenAI SDK
        would retry. The body never echoes the path.
        """
        method = _method(scope)
        path = _path(scope)
        if path in _PROBES:
            await self._readyz(send, path)
            return
        if path == "/metrics":
            await self._metrics(send)
            return
        handler = self.routes.get((method, path))
        if handler is None:
            prefixed = self._match_prefix(method, path)
            if prefixed is not None:
                # The prefixed handler reads ``scope["path"]`` to extract the ``{id}`` (Decision 2).
                await prefixed(scope, receive, send)
                return
            await emit_response(send, self._routing_miss(path))
            return
        await handler(scope, receive, send)

    def _routing_miss(self, path: str) -> errors.HTTPResponse:
        """405 with ``allow`` when ``path`` is served under another method, else 404."""
        allowed = sorted(
            {m for m, p in self.routes if p == path}
            | {m for m, prefix in self.prefix_routes if path.startswith(prefix) and path != prefix}
        )
        if not allowed:
            return errors.render_http("endpoint_not_found")
        response = errors.render_http("method_not_allowed")
        return replace(response, headers=(*response.headers, ("allow", ", ".join(allowed))))

    def _match_prefix(self, method: str, path: str) -> RouteHandler | None:
        """Longest-prefix match over ``prefix_routes`` (Decision 2); ``None`` on no match.

        Returns the handler of the LONGEST registered ``(method, prefix)`` whose ``prefix`` is a
        PROPER prefix of ``path`` (``path`` starts with ``prefix`` AND ``len(path) > len(prefix)``,
        a NON-EMPTY trailing segment). The handler reads ``scope["path"]`` to extract the ``{id}``.
        An empty trailing segment (``/v1/models/`` exactly) does NOT match → falls through to
        ``not_implemented``, keeping the exact ``/v1/models`` key as the list route. Runs ONLY on an
        exact-match miss, so no other route's behaviour changes.
        """
        best: RouteHandler | None = None
        best_len = -1
        for (route_method, prefix), route_handler in self.prefix_routes.items():
            if (
                route_method == method
                and path.startswith(prefix)
                and len(path) > len(prefix)
                and len(prefix) > best_len
            ):
                best = route_handler
                best_len = len(prefix)
        return best

    async def _readyz(self, send: ASGISend, path: str = "/readyz") -> None:
        """200 when ready (``/livez``: alive, never gated). ``/health`` is ``{"status":"ok"}``."""
        ready, reason = (True, None) if path == "/livez" else self.readiness()
        if ready:
            await emit_response(send, _ok_body(path))
            return
        await emit_response(send, _not_ready_body(reason))

    async def _metrics(self, send: ASGISend) -> None:
        """``/metrics``: Prometheus text exposition computed OFF the serving loop (R2.6).

        The snapshot+format is CPU-bound and O(samples), so it runs in ``asyncio.to_thread`` and
        the forwarding loop is never blocked by a scrape (C38 R13.2). The body is the registry's
        text exposition verbatim.
        """
        body = await asyncio.to_thread(self.metrics_registry.exposition)
        await emit_response(send, _metrics_body(body))


# --------------------------------------------------------------------------- #
# Operational-endpoint response bodies (framework-free HTTPResponse values).
# --------------------------------------------------------------------------- #

_TEXT_PLAIN = "text/plain; charset=utf-8"
_PROM_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_READY_STATUS = 200
_PROBES = frozenset({"/readyz", "/health", "/livez"})
_NOT_READY_STATUS = 503


def _ok_body(path: str = "/readyz") -> errors.HTTPResponse:
    """200 readiness body. ``/health`` is the console JSON probe; ``/readyz`` stays ``ok``."""
    if path == "/health":
        return errors.HTTPResponse(
            status=_READY_STATUS,
            body=b'{"status":"ok"}',
            headers=(("content-type", "application/json"),),
        )
    return errors.HTTPResponse(
        status=_READY_STATUS,
        body=b"ok",
        headers=(("content-type", _TEXT_PLAIN),),
    )


def _not_ready_body(reason: str | None) -> errors.HTTPResponse:
    """The ``/readyz`` not-ready body: ``503`` naming the unavailable dependency (R2.5).

    The ``reason`` the probe returned is the body verbatim (a non-empty string from
    ``state_ready``); a missing reason falls back to a declared generic ``not ready`` so the
    body is never empty (an empty 503 would make the condition undiagnosable).
    """
    detail = reason if reason else "not ready"
    return errors.HTTPResponse(
        status=_NOT_READY_STATUS,
        body=detail.encode("utf-8"),
        headers=(("content-type", _TEXT_PLAIN),),
    )


def _metrics_body(exposition: str) -> errors.HTTPResponse:
    """The ``/metrics`` body: ``200`` with the Prometheus text exposition."""
    return errors.HTTPResponse(
        status=_READY_STATUS,
        body=exposition.encode("utf-8"),
        headers=(("content-type", _PROM_MEDIA_TYPE),),
    )


# --------------------------------------------------------------------------- #
# Scope helpers + injected defaults.
# --------------------------------------------------------------------------- #


def _method(scope: ASGIScope) -> str:
    """The HTTP method from an ASGI ``http`` scope (upper-cased; empty when absent)."""
    value = scope.get("method")
    return value.upper() if isinstance(value, str) else ""


def _path(scope: ASGIScope) -> str:
    """The request path from an ASGI ``http`` scope (empty when absent)."""
    value = scope.get("path")
    return value if isinstance(value, str) else ""


def _always_ready() -> tuple[bool, str | None]:
    """The default readiness probe for an unwired deployment (parity with ``state_ready(None)``)."""
    return True, None


def _resolve_readiness(
    *,
    readiness: ReadinessProbe | None,
    metrics_registry: MetricsRegistry,
    state_view: StampView | None,
) -> ReadinessProbe:
    """Compose the ``/readyz`` probe: state FIRST, then the metrics directory (R10, task 12.1).

    The resolution mirrors the backward-compatibility contract documented on :func:`build_app`:

    * **Directory-bearing registry (the GW14d Publisher).** When ``metrics_registry`` is a
      :class:`MetricsDirectorySource` (it exposes ``directory()``), the metrics-directory gate is
      composed on top of the state gate:

      - no explicit ``readiness`` ⇒ the probe is exactly
        :func:`~gateway_v2.edge.readiness.make_metrics_readiness` over ``state_view`` + the
        registry's directory (state gated first, then the directory — R10.4/R10.1);
      - an explicit ``readiness`` ⇒ that probe is the FIRST (state-equivalent) gate and the
        directory gate is appended after it, so a deployment that already composed its own state
        probe keeps it, extended by the directory availability gate (R10.5: both evaluated, state
        first).

    * **Plain registry (``exposition()`` only).** No directory is available to compose, so the
      prior behaviour is returned UNCHANGED: the explicit ``readiness`` if given, else
      :func:`_always_ready`. Every existing caller/test that injects a plain registry is therefore
      untouched.
    """
    directory = _registry_directory(metrics_registry)
    if directory is None:
        # No metrics directory to gate on — prior behaviour, byte-for-byte unchanged.
        return readiness if readiness is not None else _always_ready
    if readiness is None:
        # The canonical composition: state_ready(state_view) first, then the directory (R10).
        return make_metrics_readiness(state_view, directory)
    # An explicit state probe is honoured FIRST; the directory gate extends it (R10.4/R10.5).
    return _compose_directory_after(readiness, directory)


def _registry_directory(metrics_registry: MetricsRegistry) -> DirectoryGate | None:
    """The registry's metrics directory if it is a :class:`MetricsDirectorySource`, else ``None``.

    A plain :class:`MetricsRegistry` (only ``exposition()``) returns ``None`` so the caller falls
    back to the prior readiness behaviour. The concrete GW14d Publisher returns its backing
    Cardinality_Directory, which satisfies :class:`~gateway_v2.edge.readiness.DirectoryGate`.
    """
    if isinstance(metrics_registry, MetricsDirectorySource):
        return metrics_registry.directory()
    return None


def _compose_directory_after(
    base: ReadinessProbe, directory: DirectoryGate
) -> ReadinessProbe:
    """A probe that runs ``base`` FIRST, then the metrics-directory gate (R10.4/R10.5).

    When ``base`` reports not ready, its reason wins and the directory is not consulted (state
    gated first, exactly one reason — R10.4). Only when ``base`` is ready is the directory checked;
    an unavailable directory yields ``(False, METRICS_DIRECTORY_REASON)`` (R10.1/R10.3). Both gates
    are evaluated every call (R10.5): the closure caches no verdict.
    """

    def probe() -> tuple[bool, str | None]:
        ready, reason = base()
        if not ready:
            return False, reason
        if not directory.available():
            return False, METRICS_DIRECTORY_REASON
        return True, None

    return probe


# --------------------------------------------------------------------------- #
# Module-level app sentinel.
# --------------------------------------------------------------------------- #

#: ``None`` until a deployment calls :func:`build_app` with its injected dependencies. The old
#: ``app = None`` stub is kept as the un-built default so ``from gateway_v2.edge import app``
#: still imports; a real server boots the app with ``build_app(...)``.
app: ASGIApplication | None = None
