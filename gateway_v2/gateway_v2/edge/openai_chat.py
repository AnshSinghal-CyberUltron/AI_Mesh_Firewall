"""Chat_Surface adapter + the §10.4 request lifecycle (GW15, task 2).

``edge/openai_chat.py`` is the orchestration one layer up from the pure wire codec
(``edge/wire/openai.py``): it runs the ordered pre-provider pipeline for the
OpenAI-compatible ``/v1/chat/completions`` and ``/v1/completions`` surfaces and
then hands off to either the shipped streaming orchestration
(``edge/routes.py::ChatRoute._stream``) or the new non-streaming collector. Every
security decision lives HERE (the wire module makes none, R1.5); every pipeline
mechanism it drives — GW05b/c identity + freshness, the plan round trip, GW19
admission, GW06 budget lease, the GW07/GW08 input detector + the output guard —
is already shipped and is WIRED, never rebuilt.

``edge`` is the top of the import-linter layer order, so this module may import
every layer below plus its sibling ``edge`` modules. The injected detector /
resolver / ``ProviderClient`` arrive through
:class:`~gateway_v2.edge.routes.ChatRoute` (in ``edge``, above ``detect``) so
``egress`` never names ``detect`` (R7.5 / R14.1).

**The §10.4 pipeline (tasks 2.1–2.4).** :meth:`ChatSurface.run_lifecycle` runs the
ordered pre-provider phases — pre-checks → parse → identity → the ONE shared-state
plan round trip → admit → budget → input scan — each a clearly-named private step
that FAILS CLOSED (it can only reduce the request to a ``refuse_code`` via an early
return, never forward on uncertainty) and each running BEFORE the provider is
opened, so a refusal makes zero provider calls. The proceed outcome carries the
plan-derived ``destination`` (``DispatchRouter.select``) + the transformed ``body``
(``Transform.to_provider``).

**The non-streaming path (task 4.1).** :meth:`ChatSurface.collect_and_render` is the
hand-off for a non-streaming request: it drains the provider, enforces every
Output_Channel (via :func:`~gateway_v2.edge.openai_nonstream.collect_and_enforce`),
and renders ONE OpenAI JSON — or withholds the body fail-closed on a BLOCK /
uncomputable decision — through the single ``emit_response`` / ``edge/errors`` path.

**No capacity literal, no module-level mutable.** The pre-check bounds all derive
from the injected ``ResourceContract`` (task 2.2); ``edge`` embeds none. The
refuse-code constants are plain module-level strings (codes, not capacity-position
literals, mirroring ``domain/posture.py``); there is no module-level list / dict /
set.

**Import cycle.** ``ChatSurface``'s ``deps`` IS a
:class:`~gateway_v2.edge.routes.ChatRoute`, so a runtime import of ``routes`` would
be circular. Resolved the house way: ``from __future__ import annotations`` plus a
:data:`typing.TYPE_CHECKING`-guarded ``ChatRoute`` reference used only in
annotations — the name never exists at runtime, so no circular import is formed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.dispatch.transform import GatewayRequest
from gateway_v2.domain import Disposition, Finding
from gateway_v2.domain.plan import ExecutionPlan, PlanUnavailable, PlanUnknownTenant, Surface
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE
from gateway_v2.edge import errors
from gateway_v2.edge.error_frame_scan import SCAN_ERROR_CODE
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.edge.openai_nonstream import collect_and_enforce, ok_response
from gateway_v2.edge.routes import ASGIScope, ASGISend, emit_response
from gateway_v2.edge.wire.openai import CanonicalRequest, MalformedRequest
from gateway_v2.plan.lookup import lookup_plan

#: The two cheap structural-part delimiters the Pre_Identity_Clock part-count proxy counts in the
#: RAW body bytes, WITHOUT a JSON parse (C26). Every OpenAI message object and every content part
#: opens with a ``{``; the request's ``messages`` / ``content`` / ``tool_calls`` arrays separate
#: their elements with ``,``. Counting these two byte occurrences is a strict over-estimate of the
#: real part count (string literals and nested objects also contribute), so the pre-check is
#: conservative: it can only ADD a refusal to a request whose structural fan-out is large, and it
#: stays O(n) over the raw bytes — never a ``json.loads`` of a potentially huge body (the heavy
#: work this phase exists to run in FRONT of). These are byte values the proxy scans for, not
#: capacity-position literals.
_PART_OPEN_BYTE = 0x7B  # ``{``
_PART_SEP_BYTE = 0x2C  # ``,``

#: The ASCII ceiling (code points <= this are plain ASCII and do not expand under NFKC). A byte /
#: code point above it is "expansion-prone": NFKC-folding compatibility, fullwidth, and ligature
#: code points is exactly what blew a 1 MiB body up 18x in the C26 register. A byte-range marker
#: for the cheap O(n) expansion proxy, not a capacity-position literal.
_ASCII_MAX_BYTE = 0x7F

#: The ASGI ``scope`` key carrying the request path, and the two chat-surface paths this handler
#: serves. Both the chat-completions and the legacy completions paths map to ``Surface.CHAT`` (the
#: plan vocabulary has no separate completions surface — a completion is a chat turn without the
#: role envelope). A ``MappingProxyType`` so the one module-level path table is immutable (no
#: module-level mutable, gated); the strings are route paths, not capacity-position literals.
_SCOPE_PATH_KEY = "path"
_PATH_TO_SURFACE: Mapping[str, Surface] = MappingProxyType(
    {
        "/v1/chat/completions": Surface.CHAT,
        "/v1/completions": Surface.CHAT,
    }
)

#: The prefix of the gateway-minted per-request id (``zs-`` — the shipped request-id convention,
#: PIPELINE-0023). The §10.4 lifecycle mints ONE request_id per request and threads it into BOTH
#: ``AdmissionController.admit`` and ``QuotaComponent.evaluate`` so a shed / budget verdict echoes
#: the SAME id (and the two phases never disagree). The id is NOT taken from any client-supplied
#: header (the PIPELINE-0023 conflation lesson — a client ``X-Request-ID`` would let two concurrent
#: requests collide); it is derived gateway-side. A plain string prefix, not a capacity-position
#: literal.
_REQUEST_ID_PREFIX = "zs-"

#: Hex width of the request-id fingerprint folded from the request identity. Wide enough that two
#: distinct (org, request-content) pairs do not collide in practice, short enough to keep the id
#: legible in a trace. A formatting width, not a capacity bound.
_REQUEST_ID_HEX_WIDTH = 24
#: Field separator for the canonical request-identity string hashed into the id. A control
#: character that cannot occur in an org id, a model name, or a channel name, so the join is
#: injective. A formatting constant, not a capacity bound.
_REQUEST_ID_SEP = "\x1f"

if TYPE_CHECKING:
    # ``ChatRoute`` lives in the sibling ``edge/routes.py`` which imports NOTHING
    # from this module at runtime; here it is used purely as the type of the
    # injected ``deps`` bundle. Guarded under ``TYPE_CHECKING`` (and reached only
    # through the stringised annotation ``from __future__ import annotations``
    # produces) so no runtime circular import is formed. Task 5 ADDS the GW15 deps
    # (parser / renderer / input_detector / identity / snapshot_source / admission
    # / quota / exports) to ``ChatRoute``; this task only type-references it.
    from gateway_v2.edge.routes import ChatRoute

__all__ = (
    "REFUSE_BUDGET_UNAVAILABLE",
    "REFUSE_IDENTITY_UNRESOLVED",
    "REFUSE_INPUT_BLOCKED",
    "REFUSE_INPUT_SCAN_FAILURE",
    "REFUSE_LIFECYCLE_INCOMPLETE",
    "REFUSE_OUTPUT_BLOCKED",
    "REFUSE_PLAN_UNAVAILABLE",
    "REFUSE_PRE_CHECK",
    "REFUSE_UNSUPPORTED_SURFACE",
    "ChatSurface",
    "LifecycleOutcome",
    "TenantIdentity",
)


# --------------------------------------------------------------------------- #
# Declared refuse-codes (fail-closed) — plain strings, not framework errors.
# --------------------------------------------------------------------------- #
#
# A phase that cannot forward sets ONE of these declared value-codes on the
# :class:`LifecycleOutcome`; the handler renders it through the single
# ``edge/errors`` ``Error_Envelope`` (an unmapped code already renders a declared
# generic error there). They are codes (the shared value-code discipline, like
# ``domain/posture.py``), NOT HTTP objects and NOT capacity-position literals.
#
# The per-phase refusals below (identity / plan / input) reuse the pre-existing
# shared spellings where one exists (``domain/posture.py`` for identity + plan,
# the shipped ``output_blocked`` for the input BLOCK gate) so the two sides of the
# pipeline never disagree on a spelling (the C37 join failure); task 2.3 / 2.4
# wire the phase bodies to those codes directly. ``REFUSE_LIFECYCLE_INCOMPLETE``
# is the SKELETON-ONLY fail-closed default an unfilled phase stub returns, so the
# lifecycle refuses (zero provider calls) rather than forwarding while a real check
# is still unimplemented; the later sub-tasks replace each stub's body and this
# default never reaches a filled phase.

#: A Pre_Identity_Clock pre-check bound was exceeded (size / expansion / part
#: count). The declared pre-check refusal; task 2.2 fills the comparison.
REFUSE_PRE_CHECK = "request_pre_check_exceeded"

#: Tenant identity could not be resolved, or its freshness stamp is stale /
#: unavailable (R4.2 / R4.3). Fail CLOSED — the shared ``shared_state_unavailable``
#: spelling (a 503, never a 401: the key may be valid). Task 2.3 fills the body.
REFUSE_IDENTITY_UNRESOLVED = "shared_state_unavailable"

#: The plan could not be resolved on the served snapshot (R2.4). The shared
#: ``plan_unavailable`` spelling. Task 2.3 fills the body.
REFUSE_PLAN_UNAVAILABLE = "plan_unavailable"

#: An input-channel finding resolved to a BLOCK disposition (C22). The request
#: refuses BEFORE ``provider.open`` (R7.2 — zero provider calls). The shipped
#: ``output_blocked``-family input spelling; task 2.4 fills the scan + resolve.
REFUSE_INPUT_BLOCKED = "input_blocked"

#: The budget lease is exhausted (R6.2). The shared ``posture.BUDGET_UNAVAILABLE`` spelling (a
#: 503, narrower than ``shared_state_unavailable``: it refuses only quota, never the whole path);
#: ``QuotaComponent.evaluate`` returns a ``BudgetVerdict`` carrying exactly this code. Task 2.4
#: fills the body. Aliased to the one shared posture spelling so the two sides never disagree.
REFUSE_BUDGET_UNAVAILABLE = BUDGET_UNAVAILABLE

#: An input-channel scan RAISED (R7, R8.4). Fail CLOSED — the request is withheld, never forwarded
#: with unscanned content. Reuses the shipped ``scan_failure`` spelling (the one the egress pipeline
#: and the error-frame scan already raise and the ``edge/errors`` envelope already renders as "the
#: output guard could not complete"), so a scan-failure refusal has one spelling across the whole
#: pipeline (C37). Task 2.4 fills the body. The non-streaming OUTPUT path (task 4.1) reuses this
#: same spelling for an uncomputable output scan / decision (R8.4), so an input-side and an
#: output-side scan failure render identically.
REFUSE_INPUT_SCAN_FAILURE = SCAN_ERROR_CODE

#: An OUTPUT-channel finding resolved to a BLOCK disposition on the non-streaming path (R8.3/R9.3).
#: The whole response body is WITHHELD and this declared code renders through the ``edge/errors``
#: envelope. Reuses the shipped ``output_blocked`` spelling — the SAME code the streaming output
#: guard's terminal error frame carries and the ``edge/errors`` envelope already maps to a 403 —
#: so the two output paths never disagree on the block spelling (C37).
REFUSE_OUTPUT_BLOCKED = "output_blocked"

#: The request reached the chat lifecycle on a path that maps to no chat ``Surface``
#: (R1.4, defensive). ``build_routes`` only dispatches ``/v1/chat/completions`` and
#: ``/v1/completions`` here, so this is unreachable through the real router; the parse
#: phase fails CLOSED with this declared code rather than guessing a surface for an
#: off-contract path. The ``edge/errors`` envelope renders an unmapped code as the
#: declared generic error, so no raw routing shape leaks.
REFUSE_UNSUPPORTED_SURFACE = "unsupported_surface"

#: SKELETON-ONLY fail-closed default (task 2.1). An unfilled phase stub returns
#: this so the lifecycle refuses rather than forwarding anything to a provider
#: while the real check is unimplemented. Replaced by each later sub-task's body;
#: never returned once a phase is filled.
REFUSE_LIFECYCLE_INCOMPLETE = "lifecycle_incomplete"


# --------------------------------------------------------------------------- #
# Value types (Data Models).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    """The result of the pre-provider §10.4 lifecycle: proceed, or refuse (R2–R7).

    Exactly one of two shapes:

    * **Proceed** — ``refuse_code is None`` and the three provider inputs are set:
      ``plan`` (the resolved :class:`~gateway_v2.domain.plan.ExecutionPlan` driving
      output enforcement), ``destination`` (``DispatchRouter.select(plan)``), and
      ``body`` (the ``Transform.to_provider`` upstream request body). The handler
      opens the provider and runs the streaming / non-streaming path.
    * **Refuse** — ``refuse_code`` is a declared value-code and the three provider
      inputs are ``None``. A non-None ``refuse_code`` means *render + stop*: the
      handler renders the code through the single ``edge/errors`` ``Error_Envelope``
      and makes ZERO provider calls. Every phase can only reduce the request to this
      shape (fail closed), never forward on uncertainty.

    Frozen + slotted: an immutable value the lifecycle returns once. The ``body``
    is a read-only ``Mapping`` (the shipped ``UpstreamRequest.body`` shape), so the
    value type carries no mutable state.
    """

    plan: ExecutionPlan | None
    destination: str | None
    body: Mapping[str, object] | None
    refuse_code: str | None
    #: The gateway-minted per-request id (:meth:`ChatSurface._request_id`) set on a refusal
    #: that occurs AFTER identity resolution (admit / budget / input-block), so the rendered
    #: ``Error_Envelope`` body + the ``x-request-id`` header echo the SAME id the admission /
    #: budget verdicts carry. ``None`` for a pre-identity refusal (pre-checks / parse /
    #: identity / plan) — that render site has no id, so the envelope renders ``request_id: null``.
    request_id: str | None = None


def _refused(code: str, *, request_id: str | None = None) -> LifecycleOutcome:
    """Build a fail-closed refusal outcome carrying ``code`` (zero provider calls).

    The one constructor for the refuse shape: ``refuse_code`` set, every provider
    input ``None``, so a refusing phase cannot accidentally leave a half-populated
    outcome that a later step might forward. ``request_id`` is threaded when the
    refusing phase ran after identity resolution; ``None`` for a pre-identity refusal. Pure.
    """
    return LifecycleOutcome(
        plan=None,
        destination=None,
        body=None,
        refuse_code=code,
        request_id=request_id,
    )


# --------------------------------------------------------------------------- #
# The Chat_Surface adapter + the §10.4 ordered pipeline (skeleton).
# --------------------------------------------------------------------------- #


class ChatSurface:
    """Runs the §10.4 ordered pre-provider pipeline; fail-closed at every phase.

    The ordered pipeline (the load-bearing phase ORDER this task pins):

    1. **pre-checks** (:meth:`_pre_checks`, task 2.2) — the Pre_Identity_Clock
       cheap size / expansion / part-count bounds, BEFORE canonicalization.
    2. **parse** (:meth:`_parse`, task 2.3) — SDK JSON → :class:`CanonicalRequest`
       via the injected parser; a malformed body → declared ``refuse_code``.
    3. **identity** (:meth:`_identity`, task 2.3) — resolve :class:`TenantIdentity`
       via ``admit.principal_of`` + the GW05b/c freshness stamp; unresolved / stale
       / unavailable → auth ``refuse_code``, fail closed.
    4. **plan** (:meth:`_plan`, task 2.3) — the ONE ``lookup_plan`` round trip on
       the served snapshot; an unresolved plan → declared ``refuse_code``.
    5. **admit** (:meth:`_admit`, task 2.4) — ``AdmissionController.admit``; a
       ``ShedVerdict`` → its ``code``.
    6. **budget** (:meth:`_budget`, task 2.4) — ``QuotaComponent.evaluate`` on the
       local lease; a ``BudgetVerdict`` → ``BUDGET_UNAVAILABLE``.
    7. **input scan** (:meth:`_input_scan`, task 2.4) — the injected detector over
       every folded Input_Channel; a BLOCK disposition (C22) → ``input_blocked``.

    Every phase runs BEFORE the provider is opened, and every phase can only return
    early with a ``refuse_code`` on failure (there is no forward-on-uncertainty
    path), so a refusal makes zero provider calls (R7.1 / R7.2). Only after all
    seven phases pass does :meth:`run_lifecycle` build the proceed
    :class:`LifecycleOutcome` with the plan-derived ``destination`` + the
    transformed ``body``.

    This task lands the method structure + ordering + the fail-closed control flow
    + the private phase seams; the phase bodies are filled by tasks 2.2–2.4.
    """

    __slots__ = ("_deps",)

    def __init__(self, *, deps: ChatRoute) -> None:
        """Construct over the injected :class:`ChatRoute` dependency bundle.

        ``deps`` carries every seam the lifecycle wires (the parser, the identity
        resolver, the served ``snapshot_source``, the admission controller, the
        quota component, the input detector + resolver, the ``DispatchRouter`` +
        ``Transform``, and the per-request exports — task 5 ADDS the GW15 fields to
        ``ChatRoute``). Held as a single frozen value so the surface itself carries
        no mutable state and is safe to share across requests.
        """
        self._deps = deps

    async def run_lifecycle(
        self,
        scope: ASGIScope,
        body: bytes,
    ) -> tuple[CanonicalRequest | None, LifecycleOutcome]:
        """Run the §10.4 ordered pipeline; return the parsed request + the outcome.

        Drives the seven phases in order, returning EARLY the instant any phase
        fails closed (a declared ``refuse_code``), so a refusal is produced before
        ``provider.open`` and makes zero provider calls. The returned tuple carries
        the :class:`CanonicalRequest` once parsing has succeeded (so the handler can
        dispatch to the streaming vs non-streaming path on ``req.streaming`` and the
        output scan can walk the same canonical shape) — it is ``None`` when the
        request was refused before parse (pre-checks / parse itself). On success the
        second element is the proceed :class:`LifecycleOutcome` (plan + destination +
        body, ``refuse_code is None``).

        """
        # Phase 1 — pre-identity cheap pre-checks (task 2.2), BEFORE canonicalization.
        pre_check = self._pre_checks(scope, body)
        if pre_check is not None:
            return None, _refused(pre_check)

        # Phase 2 — parse SDK JSON → CanonicalRequest (task 2.3). A malformed body
        # fails closed with a declared code; nothing after this runs on a bad body.
        req, parse_refuse = self._parse(scope, body)
        if parse_refuse is not None:
            return None, _refused(parse_refuse)
        if req is None:
            # Fail closed: a parse that neither produced a request nor a code is
            # treated as a refusal (never forward an un-parsed request).
            return None, _refused(REFUSE_LIFECYCLE_INCOMPLETE)

        # Phase 3 — resolve Tenant_Identity (task 2.3); stale / unavailable fails closed.
        identity, identity_refuse = await self._identity(scope)
        if identity_refuse is not None:
            return req, _refused(identity_refuse)
        if identity is None:
            return req, _refused(REFUSE_IDENTITY_UNRESOLVED)

        # Phase 4 — the ONE shared-state round trip: resolve the plan (task 2.3).
        plan, plan_refuse = self._plan(identity, req)
        if plan_refuse is not None:
            return req, _refused(plan_refuse)
        if plan is None:
            return req, _refused(REFUSE_PLAN_UNAVAILABLE)

        # Identity resolved: a refusal below echoes the minted per-request id into the
        # Error_Envelope + the x-request-id header (the id the admit / budget verdicts carry).
        request_id = self._request_id(identity, req)

        # Phase 5 — admission (task 2.4). A ShedVerdict fails closed with its code.
        admit_refuse = await self._admit(identity, req)
        if admit_refuse is not None:
            return req, _refused(admit_refuse, request_id=request_id)

        # Phase 6 — budget lease spend (task 2.4). A BudgetVerdict fails closed.
        budget_refuse = await self._budget(identity, req)
        if budget_refuse is not None:
            return req, _refused(budget_refuse, request_id=request_id)

        # Phase 7 — INPUT scan + the BLOCK short-circuit (task 2.4), the LAST step
        # before any provider contact: a BLOCK disposition refuses here, so a
        # blocked request makes zero provider calls (R7.1 / R7.2).
        input_refuse = self._input_scan(req)
        if input_refuse is not None:
            return req, _refused(input_refuse, request_id=request_id)

        # All seven phases passed: build the proceed outcome (destination + body).
        return req, self._proceed(plan, req)

    # --- phase seams (bodies filled by tasks 2.2–2.4) --------------------------------------- #

    def _pre_checks(self, scope: ASGIScope, body: bytes) -> str | None:
        """Pre_Identity_Clock cheap pre-checks (task 2.2); ``None`` ⇒ within bounds.

        The three C26 cheap pre-checks, run over the RAW ``body`` bytes BEFORE the
        auth-independent heavy canonicalization (``deps.parser.parse`` in phase 2),
        each compared against a bound read from the injected ``ResourceContract``
        (``deps.contract``) so ``edge`` embeds no capacity literal:

        1. **size** — ``len(body)`` vs ``contract.max_request_bytes()``. Caps the
           raw bytes a request may cost before any parse; the register's 7.4 s CPU
           event started with a 1 MiB body.
        2. **expansion ratio** — a cheap O(n) BYTE-level proxy (:meth:`_expansion_ratio`)
           for how much the body would grow under full NFKC canonicalization, vs
           ``contract.max_expansion_ratio()``. Computed WITHOUT running the fold,
           so the expansion bomb (18x NFKC in the register) is rejected before the
           heavy fold runs.
        3. **part count** — a cheap structural over-estimate (:meth:`_part_count`)
           of the message / content-part elements counted at the BYTE level (no
           ``json.loads``), vs ``contract.max_request_parts()``. Rejects a
           part-count flood before the fold walks every element.

        Any breach returns the declared :data:`REFUSE_PRE_CHECK` code (the handler
        renders it through ``edge/errors`` before canonicalization, R3.2); ``None``
        lets the lifecycle proceed. The phase is cheap, synchronous, and O(n) over
        the raw bytes — no I/O, no plan, no auth, no parse — because it is the
        pre-identity clock. It only ever ADDS a refusal: an in-bounds request falls
        through to the next (fail-closed) phase. ``scope`` is unused here (the
        bounds come from the contract, not the request headers).
        """
        del scope
        contract = self._deps.contract
        if len(body) > contract.max_request_bytes():
            return REFUSE_PRE_CHECK
        if self._expansion_ratio(body) > contract.max_expansion_ratio():
            return REFUSE_PRE_CHECK
        if self._part_count(body) > contract.max_request_parts():
            return REFUSE_PRE_CHECK
        return None

    @staticmethod
    def _expansion_ratio(body: bytes) -> float:
        """Cheap O(n) BYTE-level proxy for the body's NFKC expansion (C26).

        A real NFKC fold is the heavy auth-independent work this pre-check runs in
        FRONT of, so the proxy must NOT fold. Instead it counts, in one linear pass
        over the raw bytes, the share that is "expansion-prone" — any byte above the
        ASCII ceiling (:data:`_ASCII_MAX_BYTE`) is a lead/continuation byte of a
        multi-byte UTF-8 sequence, and the compatibility / fullwidth / ligature code
        points that blow up under NFKC are all encoded that way, whereas plain ASCII
        never expands. The proxy ratio is ``1 + expansion_prone / total``: a pure
        ASCII body scores ``1.0`` (no expansion), and the ratio climbs toward ``2.0``
        as the body becomes dominated by expansion-prone bytes. It is a conservative,
        monotone estimate — it never under-counts the presence of NFKC-expanding
        material — which is all the cheap gate needs to reject an expansion bomb
        before the fold. An empty body scores ``1.0`` (nothing to expand).
        """
        total = len(body)
        if total == 0:
            return 1.0
        expansion_prone = sum(1 for byte in body if byte > _ASCII_MAX_BYTE)
        return 1.0 + expansion_prone / total

    @staticmethod
    def _part_count(body: bytes) -> int:
        """Cheap structural part-count over-estimate at the BYTE level (C26).

        Counts object-open ``{`` and element-separator ``,`` bytes in the raw body
        in one linear pass, WITHOUT a ``json.loads`` of a potentially huge body (the
        tradeoff: a byte-level count is a strict over-estimate — string literals and
        nested objects also contribute these bytes — so the pre-check is
        conservative and can only ADD a refusal to a request whose structural
        fan-out is genuinely large, never wrongly admit one). This keeps the phase
        O(n) and off the heavy parse path, matching the Pre_Identity_Clock contract.
        """
        return sum(1 for byte in body if byte in (_PART_OPEN_BYTE, _PART_SEP_BYTE))

    def _parse(
        self,
        scope: ASGIScope,
        body: bytes,
    ) -> tuple[CanonicalRequest | None, str | None]:
        """Parse the SDK body → :class:`CanonicalRequest` (task 2.3).

        Reads the request ``surface`` from the ASGI ``scope["path"]`` (mapping
        ``/v1/chat/completions`` and ``/v1/completions`` to :attr:`Surface.CHAT`
        via :data:`_PATH_TO_SURFACE`) and parses ``body`` through the injected
        ``deps.parser`` (:class:`~gateway_v2.edge.wire.openai.OpenAIRequestParser`).
        A :class:`~gateway_v2.edge.wire.openai.MalformedRequest` is caught and its
        declared ``.code`` becomes the ``refuse_code`` (the second tuple element) —
        a raw framework ``json``/``Key`` error never escapes (R1.4). A path that
        maps to no chat surface fails closed with
        :data:`REFUSE_UNSUPPORTED_SURFACE` BEFORE the parser runs (the router makes
        this unreachable; the guard keeps the phase from guessing a surface).

        On success the first element is the parsed request and the code is ``None``;
        the parse makes no security decision (R1.5 — the parser is pure wire logic),
        it only turns the bytes into the canonical shape the later phases consume.
        """
        surface = self._surface_of(scope)
        if surface is None:
            return None, REFUSE_UNSUPPORTED_SURFACE
        try:
            request = self._deps.parser.parse(body, surface=surface)
        except MalformedRequest as exc:
            return None, exc.code
        return request, None

    @staticmethod
    def _surface_of(scope: ASGIScope) -> Surface | None:
        """Map the ASGI ``scope["path"]`` to a chat :class:`Surface`, or ``None``.

        Pure: reads the request path and looks it up in :data:`_PATH_TO_SURFACE`. A
        missing / non-string path, or a path this handler does not serve, returns
        ``None`` so :meth:`_parse` fails closed. No surface is guessed from an
        off-contract path.
        """
        path = scope.get(_SCOPE_PATH_KEY)
        if not isinstance(path, str):
            return None
        return _PATH_TO_SURFACE.get(path)

    async def _identity(
        self,
        scope: ASGIScope,
    ) -> tuple[TenantIdentity | None, str | None]:
        """Resolve :class:`TenantIdentity` (task 2.3); fail closed on stale/unavailable.

        Resolves the owner via the injected ``deps.identity`` resolver — the thin
        :class:`~gateway_v2.edge.identity_resolver.IdentityResolver` edge-adapter
        over ``admit.principal_of`` + the GW05b/c freshness stamp. The resolver
        reduces three fail-closed conditions to a single ``None``: no credential,
        an unresolved (revoked / never-issued) principal, and a stale / unavailable
        freshness stamp (the identity cache raises
        :class:`~gateway_v2.domain.state.StoreDataUnavailable` while unverified and
        the resolver maps it to ``None``). A ``None`` returns the declared
        :data:`REFUSE_IDENTITY_UNRESOLVED` code — a 503 ``shared_state_unavailable``,
        never a 401: the key may be valid and the uncertain part may be precisely
        the revocation (R4.2 / R4.3). A resolved :class:`TenantIdentity` returns
        ``(identity, None)``.

        **No extra round trip (R3.3).** The resolver reads only the RAM identity
        cache and the RAM-served freshness stamp (GW05b/c); it performs no request-
        path shared-state read, so the one shared-state round trip stays the plan
        ``lookup_plan`` in :meth:`_plan` and Property 6 holds.
        """
        identity = await self._deps.identity.resolve(scope)
        if identity is None:
            return None, REFUSE_IDENTITY_UNRESOLVED
        return identity, None

    def _plan(
        self,
        identity: TenantIdentity,
        req: CanonicalRequest,
    ) -> tuple[ExecutionPlan | None, str | None]:
        """The ONE shared-state round trip: resolve the plan (task 2.3).

        Performs the single ``lookup_plan(snapshot, identity.org_id, req.surface)``
        on the served ``deps.snapshot_source()`` snapshot — exactly ONE
        Shared_State_Round_Trip per request (R3.3). The snapshot source is read
        once here and nowhere else on the request path; identity (RAM cache +
        stamp) and the budget lease (local, task 2.4) add no shared-state read, so
        Property 6 holds. ``lookup_plan`` returns a ``PlanState``
        (``ExecutionPlan | PlanUnavailable | PlanUnknownTenant``): a resolved
        :class:`~gateway_v2.domain.plan.ExecutionPlan` returns ``(plan, None)``; an
        unresolved marker (:class:`~gateway_v2.domain.plan.PlanUnavailable` /
        :class:`~gateway_v2.domain.plan.PlanUnknownTenant`) returns the declared
        :data:`REFUSE_PLAN_UNAVAILABLE` code and NEVER forwards (R2.4). The
        resolution is scoped to ``identity.org_id`` — the credential's own tenant —
        so no request can resolve another tenant's plan.
        """
        snapshot = self._deps.snapshot_source()
        state = lookup_plan(snapshot, identity.org_id, req.surface)
        if isinstance(state, (PlanUnavailable, PlanUnknownTenant)):
            return None, REFUSE_PLAN_UNAVAILABLE
        return state, None

    async def _admit(
        self,
        identity: TenantIdentity,
        req: CanonicalRequest,
    ) -> str | None:
        """Admission control (task 2.4); ``None`` ⇒ admitted.

        Calls ``deps.admission.admit(owner_id, request_id, enqueued_at)``; a
        ``ShedVerdict`` returns ``verdict.code`` (the shipped controller already
        fails closed to a shed on any undecidable path, so there is no
        forward-on-uncertainty here), an ``Admitted`` returns ``None``.

        The ``owner_id`` is ``identity.org_id`` — the admission door is keyed to the
        request's own tenant, so one tenant's saturation sheds only its own requests
        (the GW19 per-owner fairness invariant). ``request_id`` is the one minted for
        this request (:meth:`_request_id`) so the ``ShedVerdict`` echoes the id the
        trace carries; ``enqueued_at`` is read from the injected clock
        (``deps.clock``) at the admission boundary, matching the shipped controller's
        injected-clock sojourn accounting (deterministic under a test's fake clock).
        """
        verdict = await self._deps.admission.admit(
            owner_id=identity.org_id,
            request_id=self._request_id(identity, req),
            enqueued_at=self._deps.clock(),
        )
        if isinstance(verdict, ShedVerdict):
            # Fail closed: the shipped controller already maps every undecidable path to a shed,
            # so a ShedVerdict is the ONLY non-admit outcome and its code is the refusal (R5.2).
            return verdict.code
        return None

    async def _budget(
        self,
        identity: TenantIdentity,
        req: CanonicalRequest,
    ) -> str | None:
        """Budget lease spend (task 2.4); ``None`` ⇒ within budget.

        Calls ``deps.quota.evaluate(cost, request_id)`` on the LOCAL lease (no
        request-path store read, R6.3); a ``BudgetVerdict`` returns its code
        (``posture.BUDGET_UNAVAILABLE`` — the one spelling the verdict carries), an
        ``Admitted`` returns ``None``.

        ``cost`` is a cheap token-count PROXY: the total length of the folded input
        channel text (:meth:`_estimated_cost`). The budget phase only needs a
        non-negative integer cost to spend against the local lease, and the input
        byte length is a monotone stand-in for the request's token cost that needs
        no tokenizer on the request path (the exact token accounting is a provider /
        GW16 concern); it is documented here as an estimate, not the final usage.
        ``request_id`` is the SAME id minted for the admission phase
        (:meth:`_request_id`, a pure function of the request identity) so a
        ``BudgetVerdict`` and a ``ShedVerdict`` echo one id. The façade's
        ``evaluate`` performs no store I/O, so this phase adds no shared-state round
        trip and Property 6 holds.
        """
        verdict = await self._deps.quota.evaluate(
            self._estimated_cost(req),
            request_id=self._request_id(identity, req),
        )
        if isinstance(verdict, Admitted):
            return None
        # A BudgetVerdict (the only non-Admitted outcome) carries posture.BUDGET_UNAVAILABLE; the
        # façade already fails closed to this on any undecidable path (R6.2 / R13.1).
        return verdict.code

    def _input_scan(self, req: CanonicalRequest) -> str | None:
        """INPUT scan + the BLOCK short-circuit (task 2.4); ``None`` ⇒ no BLOCK.

        Runs the injected ``deps.input_detector`` over every folded
        ``req.input_channels`` pair and resolves the findings via
        ``deps.resolver.decide``; a BLOCK disposition (C22 ``most_restrictive``)
        returns :data:`REFUSE_INPUT_BLOCKED` and the lifecycle returns BEFORE
        ``provider.open`` (zero provider calls, R7.2); a scan that raises fails
        closed (``scan_failure`` withhold). The LAST phase before provider contact.

        The injected ``deps.input_detector`` is a ``Callable[[str], Sequence[Finding]]``
        (the SAME findings-producing shape as the error-frame scanner, R7.5 — injected
        in ``edge``, above ``detect``, so ``egress`` names no detector). It runs over
        EVERY folded ``req.input_channels`` pair, so a canary in ANY input channel is
        scanned (C21): the per-channel findings are accumulated and resolved together
        through ``deps.resolver.decide`` — the SAME ``OutputResolver`` the output path
        uses, whose ``Decision.disposition`` is already the ``most_restrictive`` of the
        per-finding dispositions (C22, R7.4), so one BLOCK finding yields a BLOCK
        decision regardless of any co-matching REDACT/FLAG. A BLOCK disposition returns
        :data:`REFUSE_INPUT_BLOCKED`; any other disposition (ALLOW / FLAG / REDACT)
        lets the request proceed — the input phase refuses ONLY on BLOCK, and the input
        REDACT path is out of this phase's scope (the output guard masks released
        bytes).

        **Fail closed (R7, R8.4).** The detector OR the resolver raising is caught and
        mapped to :data:`REFUSE_INPUT_SCAN_FAILURE` (``scan_failure``): an uncomputable
        input scan withholds the request rather than forwarding unscanned content to a
        provider. The scan runs over the parsed, already-pre-checked channels, so no
        unbounded work escapes the Pre_Identity_Clock bounds.
        """
        try:
            findings: list[Finding] = []
            for _channel, text in req.input_channels:
                findings.extend(self._deps.input_detector(text))
            decision = self._deps.resolver.decide(findings)
        except Exception:  # noqa: BLE001 — fail-closed: a raising scan/resolve withholds (R8.4).
            return REFUSE_INPUT_SCAN_FAILURE
        if decision.disposition is Disposition.BLOCK:
            return REFUSE_INPUT_BLOCKED
        return None

    def _proceed(
        self,
        plan: ExecutionPlan,
        req: CanonicalRequest,
    ) -> LifecycleOutcome:
        """Build the proceed outcome once every phase passed (task 2.4).

        Derives the ``destination`` from ``deps.router.select(plan)`` and the
        upstream ``body`` from ``deps.transform``-shaped ``Transform.to_provider``
        over the parsed request, returning a :class:`LifecycleOutcome` with
        ``refuse_code is None``. This is the ONLY path that leaves ``refuse_code``
        unset — reached only after all seven fail-closed phases passed.

        The ``destination`` is ``deps.router.select(plan)`` — the pure, deterministic
        plan-derived upstream (the same identical destination for the same plan
        identity, so a signed retry re-routes to the same place, R2.2). The ``body``
        is ``deps.transform.to_provider(GatewayRequest(...)).body`` — the parsed
        request's ``model`` + ``text_deltas`` packed into the shipped
        ``UpstreamRequest.body`` shape (R8.4), carrying the destination through
        unchanged. The returned :class:`LifecycleOutcome` has ``refuse_code is None``;
        this is the ONLY constructor of the proceed shape, reached only after all
        seven fail-closed phases passed.
        """
        destination = self._deps.router.select(plan)
        body = self._deps.transform.to_provider(
            GatewayRequest(
                destination=destination,
                model=req.model,
                text_deltas=req.text_deltas,
            )
        ).body
        return LifecycleOutcome(
            plan=plan,
            destination=destination,
            body=body,
            refuse_code=None,
        )

    # --- request-id + cost sourcing (task 2.4) --------------------------------------------- #

    @staticmethod
    def _request_id(identity: TenantIdentity, req: CanonicalRequest) -> str:
        """Mint the ONE per-request id (task 2.4). Pure: a function of the request identity.

        The admission + budget phases both echo the same id into their verdicts, so the
        id must be STABLE across both calls within a request — a clock-minted id would
        differ between phases (the clock advances). It is therefore derived as a pure
        function of the request identity: the tenant ``org_id`` plus a fingerprint of the
        request content (``model`` + every folded input channel ``(name, text)`` pair),
        joined with an injective separator and hashed. This is NOT taken from any
        client-supplied header (two concurrent client calls sharing an ``X-Request-ID``
        would collide — the PIPELINE-0023 conflation lesson); the id is gateway-side and
        tenant-scoped. The ``zs-`` prefix matches the shipped request-id convention.

        Two genuinely distinct requests from the same tenant with byte-identical content
        hash to the same id — acceptable here because the id only needs to let a verdict
        echo the request it refused and to key the admission door; the request-content
        fingerprint keeps distinct payloads distinct, and a true per-request nonce is a
        boot-wiring concern (an injected rng) a later task can thread in without reshaping
        this seam.
        """
        parts = [identity.org_id, req.model]
        for channel, text in req.input_channels:
            parts.append(channel)
            parts.append(text)
        fingerprint = hashlib.sha256(
            _REQUEST_ID_SEP.join(parts).encode("utf-8")
        ).hexdigest()[:_REQUEST_ID_HEX_WIDTH]
        return f"{_REQUEST_ID_PREFIX}{identity.org_id}-{fingerprint}"

    @staticmethod
    def _estimated_cost(req: CanonicalRequest) -> int:
        """A cheap token-cost PROXY for the budget spend (task 2.4). Pure.

        The budget phase needs a non-negative integer cost to spend against the local
        lease; the exact token count is a provider / GW16 concern, not something to run a
        tokenizer for on the request path. The total character length of the folded input
        channel text is a monotone, O(n) stand-in — a longer prompt costs more — which is
        all the lease spend needs. Documented as an ESTIMATE, not the final usage. Pure:
        no I/O, no clock.
        """
        return sum(len(text) for _channel, text in req.input_channels)

    # --- non-streaming path (task 4.1) ----------------------------------------------------- #

    async def collect_and_render(
        self,
        req: CanonicalRequest,
        plan: ExecutionPlan,
        destination: str,
        body: Mapping[str, object],
        send: ASGISend,
    ) -> None:
        """Collect the whole upstream, enforce it, render ONE OpenAI JSON (R8/R9).

        Reached only after :meth:`run_lifecycle` produced a proceed outcome. Delegates
        the drain + per-channel scan/resolve/enforce to
        :func:`~gateway_v2.edge.openai_nonstream.collect_and_enforce` (fail closed),
        then renders: a BLOCK ⇒ withhold + ``output_blocked`` (R8.3/R9.3); an
        uncomputable scan/decision ⇒ withhold + ``scan_failure`` (R8.4), raw bytes
        NEVER emitted and no ``fail_open_total`` increment on any branch; a clean
        enforce ⇒ ONE ``chat.completion`` JSON via ``deps.renderer.non_stream_body``.
        Every reply leaves through the single ``emit_response(send, HTTPResponse)``
        path, so an unmapped value-code still renders the declared generic
        ``Error_Envelope`` body (R9.2). ``plan`` output enforcement is applied by the
        injected resolver; its mode is stamped as ``x-amf-produced-mode`` (GW13 C5/R5).
        """
        enforced = await collect_and_enforce(
            self._deps,
            destination,
            body,
            block_code=REFUSE_OUTPUT_BLOCKED,
            scan_failure_code=REFUSE_INPUT_SCAN_FAILURE,
        )
        if enforced.refuse_code is not None:
            await emit_response(send, errors.render_http(enforced.refuse_code))
            return
        json_body = self._deps.renderer.non_stream_body(req, enforced.deltas)
        # Stamp the Produced_Mode header from the plan's SELECTED mode (GW13 C5/R5).
        await emit_response(send, ok_response(json_body, plan.streaming_mode.value))
