"""The three misc OpenAI-compatible surfaces: /v1/embeddings, /v1/models, /v1/moderations (GW16).

``edge/openai_misc.py`` is the GW16 parallel of ``edge/routes.py`` (the shipped GW12/GW15
chat/completions skin): it owns the injected-dependency value the three *misc* surfaces wire
together and — in the later tasks (5.2 / 5.3 / 5.4) — the three ``Surface`` handler classes
(``EmbeddingsSurface`` / ``ModerationsSurface`` / ``ModelsSurface``) and their per-surface
lifecycles. This task (5.1) lands ONLY the :class:`MiscRoute` injected-deps value + the module
skeleton the handlers extend; the handlers are added to THIS same file by the later tasks.

**``MiscRoute`` is the GW16 twin of ``edge/routes.py::ChatRoute``.** It mirrors that value's
discipline exactly: a ``@dataclass(frozen=True, slots=True)`` built ONCE at boot and shared
across every request (every field it holds is itself stateless or per-request-reconstructed, so
nothing request-scoped is carried on the frozen route — two-tenant isolation). Where a field
implies a security decision it defaults to the shipped fail-closed default (an unwired surface
refuses rather than forwards — never a fail-OPEN hole); the non-security deps default to the
shipped stateless instances (``MiscRequestParser()`` / ``DispatchRouter()`` / ``Transform()`` /
``PerRequestExports()``), exactly as ``ChatRoute`` does.

**The critical layer constraint (R4.5).** The import-linter ``layers`` contract is
``edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts >
domain``. ``edge`` sits at the TOP, so the misc route is the ONE correct injection site for the
``input_detector`` (``detect``), the ``resolver`` (``egress``), the ``provider`` (``dispatch``),
and the ``catalog`` (``edge/app.py``): injecting them HERE — above ``detect`` — is what lets the
input scan + output resolve run through the real detector/resolver seams while ``egress`` NEVER
imports ``detect``. The misc handlers thread these injected seams down; ``egress`` names
``detect`` nowhere.

**No capacity literal, no module-level mutable.** ``MiscRoute`` holds no module-level list / dict
/ set; the defaulted deps are immutable, stateless instances (the shipped ``fail_closed_*`` /
``no_findings_detector`` / ``DispatchRouter()`` / ``Transform()`` / ``MiscRequestParser()`` /
``PerRequestExports()``), matching the ``ChatRoute`` field-default pattern; there is no
capacity-position literal in this module.

**Two deferred field defaults (importability).** ``renderer`` (``MiscResponseRenderer``, landed
by the concurrent task 2.4) and ``catalog`` (``CatalogSource`` / ``empty_catalog_source``, which
live in ``edge/app.py``) cannot be referenced as module-level defaults here:

* ``MiscResponseRenderer`` lands in ``edge/wire/openai_misc.py`` under the sibling task 2.4; its
  type is annotated under ``TYPE_CHECKING`` so this module stays importable while 2.4 lands and
  the field carries no module-level default.
* ``CatalogSource`` / ``empty_catalog_source`` live in ``edge/app.py``, which imports
  ``edge/routes.py`` — and ``routes.py`` imports THIS module (task 7.1 wires the handlers into
  ``build_routes``). A module-level ``from gateway_v2.edge.app import empty_catalog_source`` here
  would therefore form the cycle ``app.py -> routes.py -> openai_misc.py -> app.py``. The
  ``CatalogSource`` annotation is likewise ``TYPE_CHECKING``-guarded and the default is injected
  at boot (task 8.1), where ``edge/app.py`` already owns ``empty_catalog_source``.

Both ``renderer`` and ``catalog`` are therefore REQUIRED fields (no default) — the boot wiring
(task 8.1) injects the real ``MiscResponseRenderer()`` + the catalog source (or
``empty_catalog_source`` / a scripted catalog in tests), the same way ``edge/app.py`` already
builds ``ChatRoute``. ``provider`` is REQUIRED for the same reason ``ChatRoute.provider`` is: a
real ``ProviderClient`` (``StubProviderClient`` locally) must be injected — there is no safe
process-wide default provider instance. ``clock`` is REQUIRED, mirroring ``ChatRoute.clock``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.dispatch.provider import ProviderClient, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import GatewayRequest, Transform
from gateway_v2.domain import Disposition, Finding
from gateway_v2.domain.plan import (
    ExecutionPlan,
    PlanUnavailable,
    PlanUnknownTenant,
    Surface,
)
from gateway_v2.edge.asgi_sse import (
    ASGIReceive,
    ASGIScope,
    ASGISend,
    read_body,
)
from gateway_v2.edge.chat_deps import (
    AdmissionDecider,
    BudgetDecider,
    InputDetector,
    fail_closed_admission,
    fail_closed_budget,
    no_findings_detector,
    unresolved_snapshot_source,
)
from gateway_v2.edge.identity_resolver import (
    IdentityResolver,
    TenantIdentity,
    fail_closed_resolver,
)
from gateway_v2.edge.wire.openai_misc import (
    MiscEmbeddingsRequest,
    MiscModerationsRequest,
    MiscRequestParser,
    ModerationResult,
    moderation_result_from_findings,
)
from gateway_v2.egress.output_guard import MinimalOutputResolver, OutputResolver
from gateway_v2.plan.lookup import lookup_plan
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.runtime.stream_metrics import PerRequestExports

if TYPE_CHECKING:
    # Deferred imports used ONLY for the annotations below (see the module docstring):
    #   * ``MiscResponseRenderer`` lands in ``edge/wire/openai_misc.py`` under the concurrent
    #     task 2.4; importing it at runtime here would fail until 2.4 lands.
    #   * ``CatalogSource`` lives in ``edge/app.py``; a runtime import would form the cycle
    #     ``app.py -> routes.py -> openai_misc.py -> app.py`` once task 7.1 wires the handlers
    #     into ``build_routes``.
    # Both fields are REQUIRED (injected at boot, task 8.1), so no runtime default references
    # these names and the module stays importable today.
    from gateway_v2.edge.app import CatalogSource
    from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer

__all__ = (
    "EmbeddingsSurface",
    "MiscRoute",
    "ModerationsSurface",
)


# --------------------------------------------------------------------------- #
# MiscRoute (Components §2 / Data Models) — the GW16 twin of edge/routes.py::ChatRoute.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MiscRoute:
    """The injected dependencies the three misc surfaces wire together (R1.5, R4.5).

    Frozen + slotted: the route is a value built ONCE at boot and shared across requests —
    every piece it holds is itself stateless or per-request-reconstructed, so nothing
    request-scoped is carried on the frozen route (two-tenant isolation). Parallel to
    :class:`~gateway_v2.edge.routes.ChatRoute`.

    The ``input_detector`` (``detect``), the ``resolver`` (``egress``), the ``provider``
    (``dispatch``), and the ``catalog`` (``edge/app.py``) are injected HERE, in ``edge`` —
    the top layer, above ``detect`` — which is the correct injection site: ``egress`` must
    never import ``detect`` (R4.5). The misc handlers thread these injected seams down; the
    layer edge is never reached around.

    Field groups:

    * **Required (injected at boot).** ``provider`` — a real :class:`ProviderClient`
      (``StubProviderClient`` locally) must be injected; there is no safe process-wide default
      (parity with ``ChatRoute.provider``). ``clock`` — the injected monotonic clock
      (parity with ``ChatRoute.clock``). ``renderer`` — :class:`MiscResponseRenderer` from
      ``edge/wire/openai_misc.py`` (concurrent task 2.4); annotated under ``TYPE_CHECKING`` so
      this module stays importable while 2.4 lands, injected at boot. ``catalog`` — the
      :class:`CatalogSource` from ``edge/app.py``; a module-level default would cycle
      (``app.py -> routes.py -> openai_misc.py -> app.py``), so it is injected at boot (the
      boot wiring passes ``empty_catalog_source`` by default, a scripted catalog in tests).

    * **Fail-closed security defaults.** ``identity`` (``None`` ⇒ refuse
      ``shared_state_unavailable``), ``admission`` (a ``ShedVerdict`` ⇒ refuse, embeddings
      only), ``quota`` (a ``BudgetVerdict`` ⇒ refuse, embeddings only), and ``snapshot_source``
      (an unresolved plan ⇒ refuse ``plan_unavailable``, embeddings only) each default to the
      shipped fail-closed default so an unwired surface refuses rather than forwards.

    * **Stateless non-security defaults.** ``parser`` / ``input_detector`` / ``resolver`` /
      ``router`` / ``transform`` / ``exports`` default to the shipped stateless instances, the
      same field-default pattern ``ChatRoute`` uses.
    """

    # --- Required fields (no default): injected at boot (task 8.1). ---
    #: The injected upstream :class:`ProviderClient` (``StubProviderClient`` locally); embeddings
    #: forwards through it for vectors (R6.1). No safe process-wide default — a real provider must
    #: be injected (parity with ``ChatRoute.provider``).
    provider: ProviderClient
    #: The injected monotonic clock (parity with ``ChatRoute.clock``), used by the per-surface
    #: lifecycle's time-bounded phases (e.g. admission ``enqueued_at``).
    clock: Callable[[], float]
    #: The misc OpenAI response renderer (:class:`MiscResponseRenderer`, concurrent task 2.4).
    #: Annotated under ``TYPE_CHECKING`` so this module stays importable while 2.4 lands; the
    #: boot wiring injects ``MiscResponseRenderer()``.
    renderer: MiscResponseRenderer
    #: The injected Allowed_Model_Catalog source (:class:`CatalogSource`, ``edge/app.py``), read by
    #: the Models surface (R7.4). A module-level default would cycle; the boot wiring injects it
    #: (``empty_catalog_source`` by default, a scripted catalog in tests).
    catalog: CatalogSource

    # --- Fail-closed security defaults. ---
    #: The GW05b/c identity resolver (R5.1): ``None`` / stale / unavailable ⇒ the lifecycle refuses
    #: with the declared ``shared_state_unavailable`` code (zero provider calls). Defaults to the
    #: shipped fail-closed :data:`fail_closed_resolver` (every request refuses) until boot wires the
    #: real resolver.
    identity: IdentityResolver = fail_closed_resolver
    #: The GW19 admission decider (embeddings only, R5.3): a ``ShedVerdict`` ⇒ refuse with
    #: ``overload_shed`` + retry (zero provider calls). Defaults to the shipped fail-closed
    #: :data:`fail_closed_admission` (always sheds) until boot wires the real controller.
    admission: AdmissionDecider = fail_closed_admission
    #: The GW06 budget decider (embeddings only, R5.4): a ``BudgetVerdict`` ⇒ refuse with
    #: ``budget_unavailable`` (zero provider calls, local lease, no request-path store read).
    #: Defaults to the shipped fail-closed :data:`fail_closed_budget` until boot wires the real
    #: quota component.
    quota: BudgetDecider = fail_closed_budget
    #: The served plan-snapshot source (embeddings only). Embeddings is a billable, enforced
    #: upstream call, so — exactly as ``ChatRoute`` does for ``Surface.CHAT`` — it resolves the
    #: tenant's :class:`~gateway_v2.domain.plan.ExecutionPlan` for ``Surface.EMBEDDINGS`` on this
    #: served snapshot (the ONE request-path shared-state round trip) to drive ``router.select``
    #: and output enforcement (design Data Models, "``Surface`` for embeddings"). Defaults to the
    #: shipped fail-closed :func:`unresolved_snapshot_source` (every org reads ``PlanUnknownTenant``
    #: ⇒ the embeddings lifecycle refuses ``plan_unavailable``) until boot wires the real served
    #: snapshot. Moderations + Models do NOT read it (they resolve no plan surface).
    snapshot_source: Callable[[], ReplicaSnapshot] = unresolved_snapshot_source

    # --- Stateless non-security defaults (the ChatRoute field-default pattern). ---
    #: The misc request parser (``edge/wire/openai_misc.py``), pure over the request bytes. Shipped
    #: stateless default :class:`MiscRequestParser` so every construction compiles.
    parser: MiscRequestParser = MiscRequestParser()
    #: The injected input detector (``detect``, R4.1 / R8.1): the embeddings + moderations input
    #: scan runs it over EACH folded input item. Injected HERE in ``edge`` (above ``detect``) so
    #: ``egress`` never imports ``detect`` (R4.5). Defaults to the shipped
    #: :func:`no_findings_detector` until boot wires the real GW07/GW08 detector.
    input_detector: InputDetector = no_findings_detector
    #: The injected C22 output resolver (``egress``, R4.4): the input scan resolves findings via
    #: ``resolver.decide`` (``most_restrictive`` — a BLOCK short-circuits before any provider
    #: contact). Injected HERE in ``edge`` so ``egress`` never imports ``detect``. Defaults to the
    #: shipped :class:`MinimalOutputResolver` (the GW12b minimal resolver GW15 uses).
    resolver: OutputResolver = MinimalOutputResolver()
    #: The deterministic plan-derived upstream selector (``dispatch``). Shipped stateless default
    #: :class:`DispatchRouter`.
    router: DispatchRouter = DispatchRouter()
    #: The byte-verified request shaper (``dispatch``): embeddings packs ``UpstreamRequest.body``
    #: from the parsed request. Shipped stateless default :class:`Transform`.
    transform: Transform = Transform()
    #: The GW14d per-request telemetry producer (R10). Every terminal misc outcome populates its
    #: dimensions through this ONE producer content-free; ``fail_open_total`` is pinned at zero and
    #: no raw request/response content ever reaches an export field. Defaults to a fresh
    #: :class:`PerRequestExports` (the ``ChatRoute.exports`` field-default pattern) until the boot
    #: card injects the process producer.
    exports: PerRequestExports = PerRequestExports()


# --------------------------------------------------------------------------- #
# Response-shape constants + the fail-closed vector-transport signal.
# --------------------------------------------------------------------------- #
#: The OpenAI success response status / media type / header name for a non-streaming misc
#: body. Response-shape values, NOT capacity-position literals (the serving bounds live in
#: ``runtime/resources.py``); mirrors ``edge/openai_chat.py``'s non-stream constants.
_OK_HTTP_STATUS = 200
_JSON_MEDIA_TYPE = "application/json"
_CONTENT_TYPE_HEADER = "content-type"

#: The model echoed in a ``POST /v1/moderations`` body when the request omits ``model``
#: (OpenAI's moderations ``model`` is optional). A plain response-shape string, NOT a
#: capacity-position literal (serving bounds live in ``runtime/resources.py``): the renderer
#: only echoes it and the SDK accepts whatever ``model`` the body carries, so a stable
#: default keeps the render deterministic.
_DEFAULT_MODERATION_MODEL = "text-moderation-latest"


class _VectorTransportError(Exception):
    """A provider delivery could not be read as an embedding vector (fail-closed signal).

    Raised while draining the provider stream when an event carries an ``error_frame`` or a
    delta text that is not a JSON float array. The surface catches it and WITHHOLDS the body
    with the declared ``scan_failure`` code — no partial or raw provider body is ever emitted
    (R6.1 / R10). A plain internal exception, never surfaced to the client as itself.
    """


# --------------------------------------------------------------------------- #
# Shared emit helpers (the single ``edge/errors`` render path, R9).
# --------------------------------------------------------------------------- #
#
# Both misc handlers render every refusal through the ONE value-code → HTTP
# ``Error_Envelope`` and every 200 success through the ONE ``application/json``
# ``HTTPResponse``. These two module-level functions ARE that single render path,
# lifted here so neither handler re-spells it and the surfaces never disagree.
# ``emit_response`` + ``edge/errors`` are deferred-imported INSIDE each helper to keep
# this module free of the cycle task 7.1's ``routes -> openai_misc`` wiring would
# otherwise close — the discipline ``ChatRoute.handle`` uses.


async def _refuse(send: ASGISend, code: str) -> None:
    """Render a declared refuse-code through the single ``Error_Envelope`` (R9).

    Every refusal leaves through ``emit_response(send, errors.render_http(code))`` — the
    one value-code → HTTP path — so an unmapped code still renders the declared generic
    error rather than a raw framework 500 (R9.2). Touches no exports counter;
    ``fail_open_total`` is never incremented (R10.3) and the withholding sites record a
    content-free withholding on the exports producer BEFORE refusing.
    """
    from gateway_v2.edge import errors
    from gateway_v2.edge.routes import emit_response

    await emit_response(send, errors.render_http(code))


async def _emit_json(send: ASGISend, body: bytes) -> None:
    """Emit a rendered JSON body as an HTTP 200 ``application/json`` response (R9).

    The single success-emit path shared by both misc handlers: a byte-stable renderer
    output goes out through the ONE ``emit_response`` + ``HTTPResponse`` boundary with the
    OpenAI success status + media type. Deferred-imports for the same cycle reason as
    :func:`_refuse`.
    """
    from gateway_v2.edge import errors
    from gateway_v2.edge.routes import emit_response

    await emit_response(
        send,
        errors.HTTPResponse(
            status=_OK_HTTP_STATUS,
            body=body,
            headers=((_CONTENT_TYPE_HEADER, _JSON_MEDIA_TYPE),),
        ),
    )


# --------------------------------------------------------------------------- #
# EmbeddingsSurface (Components §2 / Per-surface lifecycle: embeddings) — task 5.2.
# --------------------------------------------------------------------------- #
#
# ``POST /v1/embeddings`` is the one BILLABLE misc surface, so it runs the SAME
# ordered §10.4 pre-provider lifecycle GW15's ``ChatSurface`` proved — parse →
# identity → plan → admission → budget → input scan → provider — trimmed to the
# embeddings shape (the embeddings body has no streaming disposition and no output
# scan; the vectors come straight from the injected provider). The phase ORDER is
# load-bearing: every refusal returns BEFORE ``provider.open``, so a blocked /
# shed / budget-refused / unauthenticated / plan-less request makes ZERO provider
# calls (R4.2 / R5.2 / R5.3 / R5.4 / R5.6). Every phase can only FAIL CLOSED (an
# early return with a declared ``refuse_code``); there is no forward-on-uncertainty
# path.
#
# **Why a plan round trip (the task's §-5.2 decision).** The design's Data Models
# section is explicit — "embeddings plan resolution + routing use ``Surface.EMBEDDINGS``
# exactly as chat uses ``Surface.CHAT``" — and embeddings is billable + enforced, so
# it mirrors ``ChatSurface`` and resolves the tenant's ``ExecutionPlan`` on the served
# ``deps.snapshot_source()`` snapshot (the ONE request-path shared-state round trip)
# to drive ``deps.router.select(plan)``. ``MiscRoute`` therefore carries a
# ``snapshot_source`` field (added in this task, defaulting to the shipped fail-closed
# ``unresolved_snapshot_source`` — parity with ``ChatRoute.snapshot_source``); an
# unresolved plan refuses ``plan_unavailable`` before any provider contact.
#
# **The refuse-code spellings are GW15's — reused, never re-spelled.** The identity /
# admission / budget / input-blocked / scan-failure / plan-unavailable codes are
# imported from ``edge/openai_chat.py`` (where GW15 declared them as the single shared
# spellings, each aliased to the one ``domain/posture.py`` / egress spelling) so the
# two surfaces never disagree on a code (the C37 join-failure lesson). They are
# imported function-locally inside :meth:`EmbeddingsSurface.handle` alongside
# ``emit_response`` because ``edge/openai_chat.py`` and ``edge/routes.py`` import
# ``edge/routes`` / each other at module level and task 7.1 will wire
# ``routes -> openai_misc``; a module-level import here would close that cycle, so the
# lifecycle follows the SAME deferred-import discipline ``ChatRoute.handle`` uses for
# ``ChatSurface`` (R1.5 — the cycle is avoided the house way, not by re-declaring codes).
#
# **How embedding vectors ride the provider stream (the task's provider-transport
# decision).** The shipped ``dispatch/provider.py::UpstreamEvent`` carries ONLY
# ``text_deltas`` / ``final`` / ``error_frame`` — it has NO embedding-vector field, and
# the design forbids changing the shipped ``ProviderClient`` / ``UpstreamEvent`` protocol.
# So the vectors ride the ONE transport the protocol offers: each ``UpstreamEvent``
# ``text_deltas`` pair's TEXT is a JSON-encoded array of floats = one embedding vector,
# and the arrival order of the deltas is the batch index order (``index`` = position in
# the drained stream, matching the input item position, R6.3). The surface DRAINS the
# stream exactly as ``edge/openai_nonstream.py`` does, decodes each delta text to a
# ``tuple[float, ...]``, and renders through ``deps.renderer.embeddings_body`` — it never
# COMPUTES a vector itself (R6.1); the provider is the sole source (``StubProviderClient``
# locally, R6.4). A delta whose text is not a JSON float array fails CLOSED
# (``scan_failure`` — the provider body could not be read as vectors; no partial/raw
# result is emitted). See the orchestrator report for the gap note: the shipped protocol
# has no first-class vector channel, so this JSON-over-``text_deltas`` convention is the
# least-invasive honest local transport and the ``StubProviderClient`` scripts vectors the
# same way in tests.


class EmbeddingsSurface:
    """``POST /v1/embeddings``: identity → plan → admission → budget → input scan → provider.

    Built per request (``EmbeddingsSurface(deps=misc)``) over the frozen
    :class:`MiscRoute`, so the surface itself carries no mutable state. Runs the full
    billable lifecycle fail-closed at every phase, then — only when every phase passes —
    opens the injected :class:`~gateway_v2.dispatch.provider.ProviderClient`, drains the
    embedding vectors from the stream, and renders ONE OpenAI embeddings body with batch
    index order preserved (R6). Every terminal outcome renders through the single
    ``edge/errors`` ``Error_Envelope`` and populates the content-free
    :class:`~gateway_v2.runtime.stream_metrics.PerRequestExports` with ``fail_open_total``
    pinned at zero (R10).
    """

    __slots__ = ("_deps",)

    def __init__(self, *, deps: MiscRoute) -> None:
        """Construct over the injected :class:`MiscRoute` dependency bundle."""
        self._deps = deps

    async def handle(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """Serve a ``POST /v1/embeddings`` request end-to-end (R2–R6, R10, §5.2).

        Assembles the request ``body`` from the ASGI ``receive`` channel, parses it to a
        :class:`~gateway_v2.edge.wire.openai_misc.MiscEmbeddingsRequest` (a
        :class:`~gateway_v2.edge.wire.openai.MalformedRequest` ⇒ its declared ``.code``
        via the envelope, R2.3), then runs the ordered pre-provider lifecycle. A refusal
        at any phase renders the declared value-code through the single
        ``emit_response(send, errors.render_http(code))`` path and makes ZERO provider
        calls; a full pass opens the provider, reads the vectors, and renders the
        embeddings body. The GW15 refuse-code spellings are imported here (not at module
        level) because ``edge/routes`` imports back into this module once task 7.1 wires
        ``build_routes`` — the deferred-import discipline ``ChatRoute.handle`` uses.
        """
        from gateway_v2.edge.openai_chat import (
            REFUSE_IDENTITY_UNRESOLVED,
            REFUSE_INPUT_BLOCKED,
            REFUSE_INPUT_SCAN_FAILURE,
            REFUSE_PLAN_UNAVAILABLE,
        )
        from gateway_v2.edge.wire.openai import MalformedRequest

        body = await read_body(receive)

        # Phase 1 — parse the SDK body (pure wire logic; a malformed body fails closed).
        try:
            req = self._deps.parser.parse_embeddings(body)
        except MalformedRequest as exc:
            await _refuse(send, exc.code)
            return

        # Phase 2 — resolve Tenant_Identity (GW05b/c). None / stale / unavailable ⇒ refuse,
        # zero provider calls (R5.1 / R5.2).
        identity = await self._deps.identity.resolve(scope)
        if identity is None:
            await _refuse(send, REFUSE_IDENTITY_UNRESOLVED)
            return

        # Phase 3 — the ONE shared-state round trip: resolve the plan for Surface.EMBEDDINGS
        # (parity with ChatSurface). An unresolved plan ⇒ refuse, zero provider calls (R5.6).
        plan = self._resolve_plan(identity)
        if plan is None:
            await _refuse(send, REFUSE_PLAN_UNAVAILABLE)
            return

        # Phase 4 — admission (GW19). A ShedVerdict ⇒ refuse + retry, zero provider calls (R5.3).
        admit_refuse = await self._admit(identity, req)
        if admit_refuse is not None:
            await _refuse(send, admit_refuse)
            return

        # Phase 5 — budget lease spend (GW06), local lease / no request-path store read. A
        # BudgetVerdict ⇒ refuse, zero provider calls (R5.4 / R5.5).
        budget_refuse = await self._budget(identity, req)
        if budget_refuse is not None:
            await _refuse(send, budget_refuse)
            return

        # Phase 6 — INPUT scan + the BLOCK short-circuit, the LAST phase before provider
        # contact. A BLOCK (C22) ⇒ input_blocked; a raising scan ⇒ scan_failure. Either way
        # the request returns here, so a blocked request makes ZERO provider calls (R4).
        input_refuse = self._input_scan(req, block_code=REFUSE_INPUT_BLOCKED)
        if input_refuse is not None:
            # A scan that RAISED is a withholding (fail-closed), recorded content-free (R10.3).
            if input_refuse == REFUSE_INPUT_SCAN_FAILURE:
                self._deps.exports.record_withheld()
            await _refuse(send, input_refuse)
            return

        # All phases passed: open the provider, read the vectors, render (R6). The provider is
        # reached ONLY here — after every fail-closed phase.
        await self._collect_and_render(
            req,
            plan,
            send,
            scan_failure_code=REFUSE_INPUT_SCAN_FAILURE,
        )

    # --- lifecycle phases (mirror ChatSurface, trimmed to the embeddings shape) ----------- #

    def _resolve_plan(self, identity: TenantIdentity) -> ExecutionPlan | None:
        """Resolve the tenant's plan for ``Surface.EMBEDDINGS`` (the ONE round trip).

        Performs ``lookup_plan(deps.snapshot_source(), identity.org_id, Surface.EMBEDDINGS)``
        on the served snapshot — exactly as ``ChatSurface._plan`` does for ``Surface.CHAT``,
        scoped to the credential's own tenant so no request resolves another tenant's plan.
        A resolved :class:`~gateway_v2.domain.plan.ExecutionPlan` is returned; an unresolved
        marker (:class:`~gateway_v2.domain.plan.PlanUnavailable` /
        :class:`~gateway_v2.domain.plan.PlanUnknownTenant`) returns ``None`` so the lifecycle
        fails closed with ``plan_unavailable`` BEFORE any provider contact.
        """
        snapshot = self._deps.snapshot_source()
        state = lookup_plan(snapshot, identity.org_id, Surface.EMBEDDINGS)
        if isinstance(state, (PlanUnavailable, PlanUnknownTenant)):
            return None
        return state

    async def _admit(self, identity: TenantIdentity, req: MiscEmbeddingsRequest) -> str | None:
        """Admission control (GW19); ``None`` ⇒ admitted.

        Calls ``deps.admission.admit(owner_id=org_id, request_id, enqueued_at)``; a
        ``ShedVerdict`` returns its ``code`` (the shipped controller fails closed to a shed
        on any undecidable path, so a shed is the only non-admit outcome), an ``Admitted``
        returns ``None``. ``owner_id`` is the tenant's ``org_id`` (per-owner fairness);
        ``enqueued_at`` is the injected clock at the admission boundary.
        """
        verdict = await self._deps.admission.admit(
            owner_id=identity.org_id,
            request_id=self._request_id(identity, req),
            enqueued_at=self._deps.clock(),
        )
        if isinstance(verdict, ShedVerdict):
            return verdict.code
        return None

    async def _budget(self, identity: TenantIdentity, req: MiscEmbeddingsRequest) -> str | None:
        """Budget lease spend (GW06); ``None`` ⇒ within budget.

        Calls ``deps.quota.evaluate(cost, request_id=...)`` on the LOCAL lease (no
        request-path store read, R5.5); a ``BudgetVerdict`` returns its code
        (``posture.BUDGET_UNAVAILABLE``), an ``Admitted`` returns ``None``. ``cost`` is a
        cheap token-count PROXY: the total length of the folded input item text
        (:meth:`_estimated_cost`), a monotone O(n) stand-in that needs no tokenizer on the
        request path. ``request_id`` is the SAME id minted for admission so a budget and a
        shed verdict echo one id.
        """
        verdict = await self._deps.quota.evaluate(
            self._estimated_cost(req),
            request_id=self._request_id(identity, req),
        )
        if isinstance(verdict, Admitted):
            return None
        return verdict.code

    def _input_scan(self, req: MiscEmbeddingsRequest, *, block_code: str) -> str | None:
        """INPUT scan + the BLOCK short-circuit (R4); ``None`` ⇒ no BLOCK.

        Folds every ``embeddings_input`` item into a ``(channel, text)`` Scannable_Text pair
        (:func:`~gateway_v2.edge.wire.openai_misc.fold_misc_input`), runs the injected
        ``deps.input_detector`` over EACH item's text (so a canary in ANY input item is
        scanned, C21 / R4.1), accumulates the findings, and resolves them via
        ``deps.resolver.decide`` — the SAME C22 ``most_restrictive`` resolver the chat output
        path uses, so one BLOCK finding yields a BLOCK decision regardless of a co-matching
        REDACT/FLAG (R4.4). A BLOCK disposition returns ``block_code`` (``input_blocked``) and
        the lifecycle returns BEFORE ``provider.open`` (zero provider calls, R4.2). Each
        detector run is recorded content-free on the exports producer (R10.2). The detector OR
        the resolver RAISING is caught and mapped to the shipped ``scan_failure`` spelling: an
        uncomputable input scan WITHHOLDS the request rather than forwarding unscanned content
        (fail closed, R4.6).
        """
        from gateway_v2.edge.openai_chat import REFUSE_INPUT_SCAN_FAILURE
        from gateway_v2.edge.wire.openai_misc import fold_misc_input

        exports = self._deps.exports
        try:
            findings: list[Finding] = []
            for _channel, text in fold_misc_input(req.input_items, "embeddings_input"):
                exports.observe_detector_invocation()
                findings.extend(self._deps.input_detector(text))
            decision = self._deps.resolver.decide(findings)
        except Exception:  # noqa: BLE001 — fail closed: a raising scan/resolve withholds (R4.6).
            return REFUSE_INPUT_SCAN_FAILURE
        if decision.disposition is Disposition.BLOCK:
            return block_code
        return None

    # --- provider drain + render (R6) ------------------------------------------------------ #

    async def _collect_and_render(
        self,
        req: MiscEmbeddingsRequest,
        plan: ExecutionPlan,
        send: ASGISend,
        *,
        scan_failure_code: str,
    ) -> None:
        """Open the provider, drain the vectors, render the embeddings body (R6).

        Reached only after every pre-provider phase passed. Selects the destination from
        ``deps.router.select(plan)``, packs the upstream body with
        ``deps.transform.to_provider`` (the parsed request's ``model`` + input items as the
        provider body), opens the injected provider, and drains the embedding vectors from the
        ``UpstreamEvent`` stream (:meth:`_drain_vectors` — the provider is the sole source of
        vectors, R6.1). The vectors render through ``deps.renderer.embeddings_body`` with batch
        index order preserved (R6.2 / R6.3). A provider / decode failure fails CLOSED: the body
        is WITHHELD with ``scan_failure_code`` and recorded content-free (R10.3), never a
        partial or raw result. The refusal + the 200 body both leave through the shared
        :func:`_refuse` / :func:`_emit_json` render helpers (the one ``edge/errors`` path).
        """
        destination = self._deps.router.select(plan)
        upstream_body = self._deps.transform.to_provider(
            GatewayRequest(
                destination=destination,
                model=req.model,
                text_deltas=self._input_as_deltas(req),
            )
        ).body
        try:
            vectors = await self._drain_vectors(destination, upstream_body)
        except Exception:  # noqa: BLE001 — fail closed: an unreadable provider body withholds.
            self._deps.exports.record_withheld()
            await _refuse(send, scan_failure_code)
            return
        await _emit_json(send, self._deps.renderer.embeddings_body(req, vectors))

    async def _drain_vectors(
        self,
        destination: str,
        body: Mapping[str, object],
    ) -> tuple[tuple[int, tuple[float, ...]], ...]:
        """Drain the provider stream into ordered ``(index, vector)`` pairs (R6.1 / R6.3).

        The shipped ``UpstreamEvent`` has no vector field, so each event ``text_deltas``
        pair's TEXT is a JSON-encoded array of floats = one embedding vector, and the arrival
        order IS the batch index order. Iterates ``deps.provider.open(UpstreamRequest(...))``
        to exhaustion, decoding every delta's text to a ``tuple[float, ...]`` and assigning the
        next sequential ``index``; a mid-stream ``error_frame`` or a delta whose text is not a
        JSON float array RAISES (the caller fails closed — the provider body could not be read
        as vectors, so no partial/raw result is emitted). The gateway COMPUTES no vector (R6.1);
        the provider is the sole source.
        """
        request = UpstreamRequest(destination=destination, body=body)
        vectors: list[tuple[int, tuple[float, ...]]] = []
        index = 0
        async for event in self._deps.provider.open(request):
            if event.error_frame is not None:
                raise _VectorTransportError("provider returned an error frame")
            for _channel, delta in event.text_deltas:
                vectors.append((index, self._decode_vector(delta)))
                index += 1
        return tuple(vectors)

    @staticmethod
    def _decode_vector(delta: str) -> tuple[float, ...]:
        """Decode one provider delta's text into an embedding vector (R6.1).

        The text is a JSON array of numbers; anything else RAISES so the caller fails closed.
        The gateway only READS the provider's vector — it never computes or synthesises one.
        """
        decoded = json.loads(delta)
        if not isinstance(decoded, list):
            raise _VectorTransportError("provider delta is not a JSON array")
        values: list[float] = []
        for element in decoded:
            if isinstance(element, bool) or not isinstance(element, (int, float)):
                raise _VectorTransportError("provider vector element is not a number")
            values.append(float(element))
        return tuple(values)

    @staticmethod
    def _input_as_deltas(req: MiscEmbeddingsRequest) -> tuple[tuple[str, str], ...]:
        """Pack the embeddings input items into the ``Transform`` ``text_deltas`` shape.

        The ``Transform`` consumes the ``(channel, text)`` delta shape; the embeddings input
        items ride under the ``embeddings_input`` channel so the request → provider body
        carries the exact input text the provider embeds, in request order.
        """
        return tuple(("embeddings_input", item) for item in req.input_items)

    # --- request-id + cost sourcing (mirror ChatSurface) ----------------------------------- #

    @staticmethod
    def _request_id(identity: TenantIdentity, req: MiscEmbeddingsRequest) -> str:
        """Mint the ONE per-request id. Pure: a function of the request identity.

        Mirrors ``ChatSurface._request_id`` — a STABLE id across the admission + budget
        phases (a clock-minted id would differ between phases). Derived from the tenant
        ``org_id`` + a fingerprint of the request content (``model`` + every input item),
        joined with an injective separator and hashed. NOT taken from a client header (the
        PIPELINE-0023 conflation lesson); the ``zs-`` prefix matches the request-id convention.
        """
        import hashlib

        sep = "\x1f"
        parts = [identity.org_id, req.model, *req.input_items]
        fingerprint = hashlib.sha256(sep.join(parts).encode("utf-8")).hexdigest()[:24]
        return f"zs-{identity.org_id}-{fingerprint}"

    @staticmethod
    def _estimated_cost(req: MiscEmbeddingsRequest) -> int:
        """A cheap token-cost PROXY for the budget spend. Pure.

        The total character length of the folded input items — a monotone, O(n) stand-in for
        the request's token cost that needs no tokenizer on the request path (the exact token
        accounting is a provider concern). Documented as an ESTIMATE, not final usage.
        """
        return sum(len(item) for item in req.input_items)


# --------------------------------------------------------------------------- #
# ModerationsSurface (Components §2 & §3 / Per-surface lifecycle: moderations) — task 5.3.
# --------------------------------------------------------------------------- #
#
# ``POST /v1/moderations`` is the SCAN-ONLY misc surface: a trimmed lifecycle —
# identity → input scan → render findings-as-categories. It is NOT billable, so it
# runs NO admission / budget / plan phase and — the hard gate — NEVER opens the
# provider (R8.5); the only shared-state access is the ONE identity resolve.
#
# Fail CLOSED on an uncomputable scan (R8.6): moderations has no provider to withhold
# from — the scan findings ARE the result — so a RAISING detector is a withholding,
# not an input-block: the surface emits NO result body (never a benign
# ``flagged:false`` unscanned result) and refuses ``scan_failure``. A clean scan with
# no findings is a legitimate ``flagged:false`` result; the two are never conflated.
#
# No ``resolver.decide`` (unlike embeddings): moderations MAPS the EXECUTED findings
# onto OpenAI moderation categories via ``moderation_result_from_findings`` (Components
# §3) — no BLOCK short-circuit, since there is no provider to short-circuit before. The
# scan-failure code + the ``_refuse`` / ``_emit_json`` helpers are the SAME shared ones
# EmbeddingsSurface uses (R1.5).


class ModerationsSurface:
    """``POST /v1/moderations``: identity → input scan → render findings-as-categories.

    Built per request (``ModerationsSurface(deps=misc)``) over the frozen
    :class:`MiscRoute`, so the surface carries no mutable state. Scan-only: resolves the
    tenant identity, folds the ``moderations_input`` items, runs the injected
    :data:`~gateway_v2.edge.chat_deps.InputDetector` over EACH item, maps the EXECUTED
    findings onto the OpenAI moderation category shape, and renders ONE moderations body —
    it opens NO provider (R8.5) and runs no admission / budget / plan phase. An uncomputable
    scan fails CLOSED with ``scan_failure`` and emits NO result body (R8.6). Every terminal
    outcome renders through the single ``edge/errors`` envelope (or the one 200 JSON path)
    and populates the content-free
    :class:`~gateway_v2.runtime.stream_metrics.PerRequestExports`, ``fail_open_total``
    pinned at zero (R10).
    """

    __slots__ = ("_deps",)

    def __init__(self, *, deps: MiscRoute) -> None:
        """Construct over the injected :class:`MiscRoute` dependency bundle."""
        self._deps = deps

    async def handle(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """Serve a ``POST /v1/moderations`` request end-to-end (R8, R10, §5.3).

        Parses the ASGI body to a
        :class:`~gateway_v2.edge.wire.openai_misc.MiscModerationsRequest` (a
        :class:`~gateway_v2.edge.wire.openai.MalformedRequest` ⇒ its declared ``.code`` via
        the envelope — a missing ``input`` renders HTTP 400 ``missing_required_parameter``,
        R2.4 / R8), resolves the tenant identity (``None`` ⇒ ``shared_state_unavailable``,
        fail closed), scans every input item, and renders the moderation body. An
        uncomputable scan withholds with ``scan_failure`` and emits NO result body (R8.6);
        the surface opens NO provider (R8.5). The refuse-code spellings are imported here
        (not at module level) to avoid the cycle ``edge/routes`` closes once task 7.1 wires
        ``build_routes`` — the deferred-import discipline ``ChatRoute.handle`` uses.
        """
        from gateway_v2.edge.openai_chat import (
            REFUSE_IDENTITY_UNRESOLVED,
            REFUSE_INPUT_SCAN_FAILURE,
        )
        from gateway_v2.edge.wire.openai import MalformedRequest

        body = await read_body(receive)

        # Phase 1 — parse (pure wire). A missing ``input`` is raised by the parser as
        # MalformedRequest(MISSING_REQUIRED_PARAMETER) → HTTP 400 (R2.4 / R8).
        try:
            req = self._deps.parser.parse_moderations(body)
        except MalformedRequest as exc:
            await _refuse(send, exc.code)
            return

        # Phase 2 — resolve Tenant_Identity. None / stale / unavailable ⇒ refuse (R8.1 / R5.1);
        # moderations opens no provider either way (R8.5).
        identity = await self._deps.identity.resolve(scope)
        if identity is None:
            await _refuse(send, REFUSE_IDENTITY_UNRESOLVED)
            return

        # Phase 3 — scan-only. A RAISING scan is a withholding (fail closed): NO result body
        # is emitted (never a flagged:false unscanned result — R8.6).
        result = self._scan(req)
        if result is None:
            self._deps.exports.record_withheld()
            await _refuse(send, REFUSE_INPUT_SCAN_FAILURE)
            return

        # Render ONE 200 moderation body; ``model`` is optional on the request so echo it or
        # the default.
        model = req.model if req.model is not None else _DEFAULT_MODERATION_MODEL
        await _emit_json(send, self._deps.renderer.moderation_body(model, result))

    def _scan(self, req: MiscModerationsRequest) -> ModerationResult | None:
        """Scan every input item and map findings → categories (R8.1–R8.4); ``None`` ⇒ withhold.

        Folds every ``moderations_input`` item into a ``(channel, text)`` Scannable_Text pair
        (:func:`~gateway_v2.edge.wire.openai_misc.fold_misc_input`), runs the injected
        ``deps.input_detector`` over EACH item's text (a canary in ANY item is scanned, C21 /
        R8.1), records each run content-free on the exports producer (R10.2), and maps the
        accumulated findings onto ONE
        :class:`~gateway_v2.edge.wire.openai_misc.ModerationResult` via
        :func:`~gateway_v2.edge.wire.openai_misc.moderation_result_from_findings` (the stock /
        extra category mapping, R8.3 / R8.4). Moderations does NOT resolve a BLOCK/REDACT
        disposition (no provider to short-circuit) — it maps the EXECUTED findings directly. A
        RAISING detector means the scan could not be computed: returns ``None`` so the surface
        fails CLOSED with ``scan_failure`` and emits NO result body (R8.6).
        """
        from gateway_v2.edge.wire.openai_misc import fold_misc_input

        exports = self._deps.exports
        try:
            findings: list[Finding] = []
            for _channel, text in fold_misc_input(req.input_items, "moderations_input"):
                exports.observe_detector_invocation()
                findings.extend(self._deps.input_detector(text))
        except Exception:  # noqa: BLE001 — fail closed: a raising scan withholds (R8.6).
            return None
        return moderation_result_from_findings(findings)
