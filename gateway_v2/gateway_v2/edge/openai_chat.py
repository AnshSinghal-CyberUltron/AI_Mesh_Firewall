"""Chat_Surface adapter + the §10.4 request lifecycle (GW15, task 2).

``edge/openai_chat.py`` is the orchestration one layer up from the pure wire codec
(``edge/wire/openai.py``): it runs the ordered pre-provider pipeline for the
OpenAI-compatible ``/v1/chat/completions`` and ``/v1/completions`` surfaces and then
hands off to the shipped streaming orchestration or the non-streaming collector.
Every security decision lives HERE (the wire module makes none, R1.5); every
mechanism it drives (identity, the plan round trip, GW19 admission, GW06 budget
lease, the GW07/GW08 detector + the output guard) is shipped and WIRED, not rebuilt.

**The §10.4 pipeline.** :meth:`ChatSurface.run_lifecycle` runs the ordered phases —
pre-checks → parse → identity → the ONE plan round trip → admit → budget → input
scan — each FAILING CLOSED (it can only reduce the request to a ``refuse_code`` via
an early return) and each running BEFORE the provider is opened, so a refusal makes
zero provider calls.

**Group A/D/E/F rewiring (v2-enforcement-leaks, task 26.1).** The input scan
delegates to :func:`~gateway_v2.edge.input_enforcement.scan_input`, resolving at
:attr:`Phase.INPUT` via :func:`~gateway_v2.edge.phase_resolvers.resolver_for` (E).
:meth:`_proceed` builds the upstream body ONLY from
:func:`~gateway_v2.edge.input_enforcement.stage_upstream`, so a REDACT decision's
transformations are APPLIED and verified before the provider and an unmaskable REDACT
refuses ``input_unmaskable`` with zero calls (A). The output budget is clamped to
v1's bounds before the cost is charged, and the cost is the v1 token estimate (F).
A per-request :class:`~gateway_v2.edge.request_holds.RequestHolds` records the
admitted slot + the budget spend so the handler releases them exactly once (D).

**Import cycle.** ``ChatSurface``'s ``deps`` IS a
:class:`~gateway_v2.edge.routes.ChatRoute`, so a runtime import of ``routes`` would be
circular. Resolved the house way: a :data:`typing.TYPE_CHECKING`-guarded ``ChatRoute``
reference used only in annotations, so no runtime circular import is formed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.dispatch.provider import UpstreamRequest
from gateway_v2.dispatch.transform import GatewayRequest
from gateway_v2.domain import Decision
from gateway_v2.domain.plan import ExecutionPlan, Phase, PlanUnavailable, PlanUnknownTenant, Surface
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE
from gateway_v2.edge import errors
from gateway_v2.edge.error_frame_scan import SCAN_ERROR_CODE
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.edge.input_enforcement import (
    REFUSE_INPUT_UNMASKABLE,
    InputScan,
    scan_input,
    stage_upstream,
)
from gateway_v2.edge.openai_nonstream import collect_and_enforce, ok_response
from gateway_v2.edge.phase_resolvers import resolver_for
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.routes import ASGIScope, ASGISend, emit_response
from gateway_v2.edge.wire.openai import (
    CanonicalRequest,
    MalformedRequest,
    clamp_output_budget,
    prompt_chars,
)
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
    # ``ChatRoute`` lives in the sibling ``edge/routes.py`` which imports NOTHING from
    # this module at runtime; here it is only the type of the injected ``deps`` bundle,
    # reached through the stringised annotations ``from __future__ import annotations``
    # produces, so no runtime circular import is formed.
    from gateway_v2.edge.routes import ChatRoute

__all__ = (
    "REFUSE_BUDGET_UNAVAILABLE",
    "REFUSE_IDENTITY_UNRESOLVED",
    "REFUSE_INPUT_BLOCKED",
    "REFUSE_INPUT_SCAN_FAILURE",
    "REFUSE_INPUT_UNMASKABLE",
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
# :class:`LifecycleOutcome`; the handler renders it through the single ``edge/errors``
# ``Error_Envelope`` (an unmapped code renders a declared generic error there). They
# are codes (the shared value-code discipline, like ``domain/posture.py``), NOT HTTP
# objects and NOT capacity-position literals. Each reuses the pre-existing shared
# spelling where one exists so the two sides of the pipeline never disagree (C37).

#: A Pre_Identity_Clock pre-check bound was exceeded (size / expansion / part count).
REFUSE_PRE_CHECK = "request_pre_check_exceeded"

#: Tenant identity unresolved, or its freshness stamp stale / unavailable (R4.2/R4.3).
#: Fail CLOSED — ``shared_state_unavailable`` (a 503, never a 401: the key may be valid).
REFUSE_IDENTITY_UNRESOLVED = "shared_state_unavailable"

#: The plan could not be resolved on the served snapshot (R2.4).
REFUSE_PLAN_UNAVAILABLE = "plan_unavailable"

#: An input-channel finding resolved to a BLOCK disposition (C22). ``stage_upstream``
#: mints no authorization for a BLOCK, so the request refuses BEFORE ``provider.open``
#: (R7.2 — zero provider calls). The shipped ``output_blocked``-family input spelling.
REFUSE_INPUT_BLOCKED = "input_blocked"

#: The budget lease is exhausted (R6.2). The shared ``posture.BUDGET_UNAVAILABLE``
#: spelling (a 503, narrower than ``shared_state_unavailable``: it refuses only quota).
REFUSE_BUDGET_UNAVAILABLE = BUDGET_UNAVAILABLE

#: An input-channel scan RAISED (R7, R8.4). Fail CLOSED — the request is withheld.
#: Reuses the shipped ``scan_failure`` spelling so an input-side and an output-side
#: scan failure render identically (C37); the non-streaming OUTPUT path reuses it too.
REFUSE_INPUT_SCAN_FAILURE = SCAN_ERROR_CODE

#: An OUTPUT-channel finding resolved to a BLOCK on the non-streaming path (R8.3/R9.3).
#: The whole response body is WITHHELD. Reuses the shipped ``output_blocked`` spelling.
REFUSE_OUTPUT_BLOCKED = "output_blocked"

#: The request reached the chat lifecycle on a path that maps to no chat ``Surface``
#: (R1.4, defensive) — unreachable through the real router; the parse phase fails CLOSED
#: here rather than guessing a surface. Rendered as the declared generic error.
REFUSE_UNSUPPORTED_SURFACE = "unsupported_surface"

#: Fail-closed default a parse that produced neither a request nor a code returns, so
#: the lifecycle refuses rather than forwarding an un-parsed request (never reached on
#: a filled phase).
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
    #: The offending request field for a field-specific parse refusal (Group F): ``"max_tokens"``
    #: for :data:`~gateway_v2.edge.wire.openai.INVALID_MAX_TOKENS` /
    #: ``"max_completion_tokens"`` for the completion-token boundary, carried from
    #: ``MalformedRequest.param``. The render site threads it into ``errors.render_http(param=...)``
    #: so the OpenAI envelope echoes ``error.param``; ``None`` for every non-field-specific code.
    refuse_param: str | None = None


def _refused(
    code: str, *, request_id: str | None = None, param: str | None = None
) -> LifecycleOutcome:
    """Build a fail-closed refusal outcome carrying ``code`` (zero provider calls).

    The one constructor for the refuse shape: ``refuse_code`` set, every provider
    input ``None``, so a refusing phase cannot accidentally leave a half-populated
    outcome that a later step might forward. ``request_id`` is threaded when the
    refusing phase ran after identity resolution (``None`` for a pre-identity
    refusal); ``param`` names the offending field for a boundary parse refusal
    (``None`` otherwise). Pure.
    """
    return LifecycleOutcome(
        plan=None,
        destination=None,
        body=None,
        refuse_code=code,
        request_id=request_id,
        refuse_param=param,
    )


# --------------------------------------------------------------------------- #
# The Chat_Surface adapter + the §10.4 ordered pipeline (skeleton).
# --------------------------------------------------------------------------- #


class ChatSurface:
    """Runs the §10.4 ordered pre-provider pipeline; fail-closed at every phase.

    The ordered phases, each BEFORE the provider is opened and each able only to
    return early with a ``refuse_code`` (no forward-on-uncertainty), so a refusal
    makes zero provider calls (R7.1/R7.2): pre-checks (:meth:`_pre_checks`) → parse
    (:meth:`_parse`) → identity (:meth:`_identity`) → the ONE plan round trip
    (:meth:`_plan`) → admit (:meth:`_admit`) → budget (:meth:`_budget`) → input scan
    (:meth:`_input_scan`). Only after every phase passes does :meth:`run_lifecycle`
    stage the VERIFIED upstream (:meth:`_proceed`, via ``stage_upstream``), where a
    BLOCK yields ``input_blocked`` and a REDACT's transformations are applied before
    the provider (the Group A leak-fix).
    """

    __slots__ = ("_deps",)

    def __init__(self, *, deps: ChatRoute) -> None:
        """Construct over the injected :class:`ChatRoute` dependency bundle.

        ``deps`` carries every seam the lifecycle wires (parser, identity resolver,
        served ``snapshot_source``, admission, quota, input detector + resolver,
        ``DispatchRouter`` + ``Transform``, phase resolvers and per-request exports).
        Held as a single frozen value so the surface carries no mutable state.
        """
        self._deps = deps

    async def run_lifecycle(
        self,
        scope: ASGIScope,
        body: bytes,
        holds: RequestHolds | None = None,
    ) -> tuple[CanonicalRequest | None, LifecycleOutcome]:
        """Run the §10.4 ordered pipeline; return the parsed request + the outcome.

        Drives the ordered phases, returning EARLY the instant any phase fails
        closed (a declared ``refuse_code``), so a refusal is produced before
        ``provider.open`` and makes zero provider calls. The returned tuple carries
        the :class:`CanonicalRequest` once parsing has succeeded (so the handler can
        dispatch to the streaming vs non-streaming path on ``req.streaming`` and the
        output scan can walk the same canonical shape) — it is ``None`` when the
        request was refused before parse (pre-checks / parse itself). On success the
        second element is the proceed :class:`LifecycleOutcome` (plan + destination +
        body, ``refuse_code is None``).

        ``holds`` is the per-request :class:`RequestHolds` ledger (Group D/F). The
        lifecycle RECORDS what this request acquired — :meth:`RequestHolds.admitted`
        after an ``Admitted`` verdict and :meth:`RequestHolds.spent` after the budget
        phase — so the handler releases the slot + refunds the spend exactly once in a
        ``finally`` (``ChatRoute.handle`` constructs + threads it; ``mark_dispatched``
        is recorded immediately before ``provider.open``). Defaults to a FRESH per-call
        ledger so a shipped caller that passes none stays callable, unshared.
        """
        if holds is None:
            holds = RequestHolds()

        # Phase 1 — pre-identity cheap pre-checks (task 2.2), BEFORE canonicalization.
        pre_check = self._pre_checks(scope, body)
        if pre_check is not None:
            return None, _refused(pre_check)

        # Phase 2 — parse SDK JSON → CanonicalRequest (task 2.3). A malformed body
        # fails closed with a declared code; nothing after this runs on a bad body.
        # A boundary-refusal (invalid_max_tokens / invalid_max_completion_tokens)
        # carries the offending field name so the render echoes ``param`` (R2.15).
        req, parse_refuse, parse_param = self._parse(scope, body)
        if parse_refuse is not None:
            return None, _refused(parse_refuse, param=parse_param)
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

        # Plan pinned — clamp the output budget to v1's bounds BEFORE the cost is
        # charged (Group F): ``n`` → ``max_completions()`` and ``max_tokens`` →
        # ``min(org ceiling, hard cap)``. The clamped req flows to the budget phase
        # and the proceed path, so a high-``n`` / high-``max_tokens`` request cannot
        # under-charge and the provider receives v1's bounded budget.
        req = clamp_output_budget(
            req,
            org_ceiling=self._deps.contract.default_response_token_ceiling(),
            contract=self._deps.contract,
        )

        # Identity resolved: a refusal below echoes the minted per-request id into the
        # Error_Envelope + the x-request-id header (the id the admit / budget verdicts carry).
        request_id = self._request_id(identity, req)

        # Phase 5 — admission (task 2.4). A ShedVerdict fails closed with its code;
        # an Admitted records the slot on ``holds`` so it is ``complete``-d on exit.
        admit_refuse = await self._admit(identity, req)
        if admit_refuse is not None:
            return req, _refused(admit_refuse, request_id=request_id)
        holds.admitted(request_id)

        # Phase 6 — budget lease spend (task 2.4). A BudgetVerdict fails closed; a
        # spend records the SAME cost on ``holds`` so it is refunded if nothing is
        # dispatched. The cost is computed ONCE and spent + recorded identically.
        cost = self._estimated_cost(req)
        budget_refuse = await self._budget(identity, req, cost)
        if budget_refuse is not None:
            return req, _refused(budget_refuse, request_id=request_id)
        holds.spent(cost, request_id)

        # Phase 7 — INPUT scan (task 2.4), the LAST step before any provider contact.
        # A detector / resolver raise fails closed with ``scan_failure``; the resolved
        # Decision is carried to ``_proceed``, where a BLOCK yields ``input_blocked``
        # (``authorize`` mints nothing) and a REDACT's transformations are APPLIED and
        # verified before the provider — so a blocked / unmaskable request makes zero
        # provider calls (R7.1 / R7.2, the Group A leak-fix).
        scan = self._input_scan(plan, req)
        if scan.refuse_code is not None or scan.decision is None:
            return req, _refused(
                scan.refuse_code or REFUSE_INPUT_SCAN_FAILURE, request_id=request_id
            )

        # All phases passed: stage the VERIFIED upstream bytes (destination + body).
        return req, self._proceed(plan, req, scan.decision, request_id)

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
    ) -> tuple[CanonicalRequest | None, str | None, str | None]:
        """Parse the SDK body → :class:`CanonicalRequest` (task 2.3).

        Reads the request ``surface`` from the ASGI ``scope["path"]`` (mapping
        ``/v1/chat/completions`` and ``/v1/completions`` to :attr:`Surface.CHAT`
        via :data:`_PATH_TO_SURFACE`) and parses ``body`` through the injected
        ``deps.parser`` (:class:`~gateway_v2.edge.wire.openai.OpenAIRequestParser`).
        A :class:`~gateway_v2.edge.wire.openai.MalformedRequest` is caught and its
        declared ``.code`` becomes the ``refuse_code`` (the second tuple element)
        plus its ``.param`` (the third — the offending field name for a boundary
        refusal such as ``invalid_max_tokens``, ``None`` for a code-only refusal),
        so a raw framework ``json``/``Key`` error never escapes (R1.4) and the field
        name reaches the render site. A path that maps to no chat surface fails
        closed with :data:`REFUSE_UNSUPPORTED_SURFACE` BEFORE the parser runs (the
        router makes this unreachable; the guard keeps the phase from guessing a
        surface).

        On success the first element is the parsed request and the code + param are
        ``None``; the parse makes no security decision (R1.5 — the parser is pure
        wire logic), it only turns the bytes into the canonical shape later phases consume.
        """
        surface = self._surface_of(scope)
        if surface is None:
            return None, REFUSE_UNSUPPORTED_SURFACE, None
        try:
            request = self._deps.parser.parse(body, surface=surface, contract=self._deps.contract)
        except MalformedRequest as exc:
            return None, exc.code, exc.param
        return request, None, None

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
        cost: int,
    ) -> str | None:
        """Budget lease spend (task 2.4); ``None`` ⇒ within budget.

        Calls ``deps.quota.evaluate(cost, request_id)`` on the LOCAL lease (no
        request-path store read, R6.3); a ``BudgetVerdict`` returns its code
        (``posture.BUDGET_UNAVAILABLE`` — the one spelling the verdict carries), an
        ``Admitted`` returns ``None``.

        ``cost`` is the v1 token estimate (:meth:`_estimated_cost`), computed ONCE in
        :meth:`run_lifecycle` and passed to BOTH this spend and ``holds.spent`` so the
        amount refunded on a zero-provider-call refusal is exactly the amount spent
        (Group F). ``request_id`` is the SAME id minted for the admission phase
        (:meth:`_request_id`, a pure function of the request identity) so a
        ``BudgetVerdict`` and a ``ShedVerdict`` echo one id. The façade's ``evaluate``
        performs no store I/O, so this phase adds no shared-state round trip and
        Property 6 holds.
        """
        verdict = await self._deps.quota.evaluate(
            cost,
            request_id=self._request_id(identity, req),
        )
        if isinstance(verdict, Admitted):
            return None
        # A BudgetVerdict (the only non-Admitted outcome) carries posture.BUDGET_UNAVAILABLE; the
        # façade already fails closed to this on any undecidable path (R6.2 / R13.1).
        return verdict.code

    def _input_scan(self, plan: ExecutionPlan, req: CanonicalRequest) -> InputScan:
        """INPUT scan over every folded channel (task 2.4 / Group A); returns the scan.

        Thin adapter over :func:`~gateway_v2.edge.input_enforcement.scan_input`, the
        ONE input scanner. It resolves the findings at :attr:`Phase.INPUT` via
        :func:`~gateway_v2.edge.phase_resolvers.resolver_for` — ``deps.phase_resolvers``
        when a route wires one (so an ``input``-scoped rule is enforced only on input,
        Group E), else today's single ``deps.resolver``. ``scan_input`` runs
        ``deps.input_detector`` once per delta AND once per message-part join run,
        shifts every span into request-content coordinates, drops exact duplicates and
        resolves ONCE (C21).

        The returned :class:`InputScan` carries BOTH the resolved
        :class:`~gateway_v2.domain.Decision` and a ``refuse_code``:

        * ``refuse_code`` is :data:`REFUSE_INPUT_SCAN_FAILURE` (``decision is None``)
          when the detector or resolver RAISED — fail closed, the request is withheld
          rather than forwarded with unscanned content (R7 / R8.4).
        * otherwise ``refuse_code is None`` and ``decision`` is carried to
          :meth:`_proceed`, which stages the VERIFIED upstream bytes: a BLOCK yields
          ``input_blocked`` (``authorize`` mints nothing) and a REDACT's
          transformations are APPLIED before the provider (the Group A leak-fix). The
          input phase no longer short-circuits a BLOCK here; the BLOCK / unmaskable
          refusal is produced in ``stage_upstream`` before any provider call.
        """
        return scan_input(
            req,
            self._deps.input_detector,
            resolver_for(self._deps, plan, Phase.INPUT),
        )

    def _proceed(
        self,
        plan: ExecutionPlan,
        req: CanonicalRequest,
        decision: Decision,
        request_id: str,
    ) -> LifecycleOutcome:
        """Stage the VERIFIED upstream once every phase passed (task 2.4 / Group A).

        Derives the ``destination`` from ``deps.router.select(plan)`` (pure,
        deterministic — a signed retry re-routes to the same place, R2.2) and builds
        the upstream body ONLY from
        :func:`~gateway_v2.edge.input_enforcement.stage_upstream` over the input
        ``decision`` — the core leak-A fix: a REDACT decision's transformations are
        APPLIED and the transformed bytes are VERIFIED (``verified_upstream``) before
        any provider is opened, so the provider receives only the sanitized bytes.

        ``stage_upstream`` returns either a declared refuse CODE (a ``str``) or an
        :class:`~gateway_v2.dispatch.provider.UpstreamRequest`:

        * a ``str`` — ``input_blocked`` (a BLOCK decision mints no authorization,
          defensive) or :data:`REFUSE_INPUT_UNMASKABLE` (a REDACT whose transformed
          bytes still carry a detected value, or whose transformation cannot be
          applied) — produces a REFUSED outcome carrying that code with the per-request
          ``request_id`` and ZERO provider calls.
        * an ``UpstreamRequest`` — the proceed outcome is built from its verified
          ``body`` + ``destination`` (the ONLY bytes that reach the provider).

        Streaming and non-streaming share this one staging path. The returned proceed
        :class:`LifecycleOutcome` has ``refuse_code is None``.
        """
        destination = self._deps.router.select(plan)
        staged = stage_upstream(
            decision,
            GatewayRequest(
                destination=destination,
                model=req.model,
                text_deltas=req.text_deltas,
            ),
            self._deps.transform,
        )
        if isinstance(staged, str):
            return _refused(staged, request_id=request_id)
        upstream: UpstreamRequest = staged
        return LifecycleOutcome(
            plan=plan,
            destination=upstream.destination,
            body=upstream.body,
            refuse_code=None,
        )

    # --- request-id + cost sourcing (task 2.4) --------------------------------------------- #

    @staticmethod
    def _request_id(identity: TenantIdentity, req: CanonicalRequest) -> str:
        """Mint the ONE per-request id (task 2.4). Pure: a function of the request identity.

        The admission + budget phases both echo the same id, so it must be STABLE across
        both calls — a clock-minted id would differ (the clock advances). Derived as a
        pure function of the request identity: the tenant ``org_id`` plus a fingerprint of
        the request content (``model`` + every folded input channel pair), joined with an
        injective separator and hashed. NOT taken from any client header (two concurrent
        client calls sharing an ``X-Request-ID`` would collide — the PIPELINE-0023 lesson);
        the id is gateway-side and tenant-scoped. The ``zs-`` prefix is the shipped convention.
        """
        parts = [identity.org_id, req.model]
        for channel, text in req.input_channels:
            parts.append(channel)
            parts.append(text)
        fingerprint = hashlib.sha256(
            _REQUEST_ID_SEP.join(parts).encode("utf-8")
        ).hexdigest()[:_REQUEST_ID_HEX_WIDTH]
        return f"{_REQUEST_ID_PREFIX}{identity.org_id}-{fingerprint}"

    def _estimated_cost(self, req: CanonicalRequest) -> int:
        """The v1 request token estimate charged against the lease (Group F, R2.15). Pure.

        Returns ``contract.estimate_request_tokens(prompt_chars(req), req.max_tokens,
        req.n)`` over the CLAMPED ``req`` — v1's ``estimate_prompt_tokens``:
        ``max(1, prompt_chars // 4)`` input tokens (0 when there are none) plus the
        requested output budget ``max_tokens × n``, at least 1. The cost and the GW06
        lease chunk are therefore in ONE unit, tokens, so a long prompt is not refused
        merely because the request-count chunk is smaller than a character count, and a
        high-``n`` / high-``max_tokens`` request cannot under-charge (bugfix.md F). The
        bound lives in the injected ``contract``, so ``edge`` embeds no literal. Pure.
        """
        return self._deps.contract.estimate_request_tokens(
            prompt_chars(req), req.max_tokens, req.n
        )

    # --- non-streaming path (task 4.1) ----------------------------------------------------- #

    async def collect_and_render(
        self,
        req: CanonicalRequest,
        plan: ExecutionPlan,
        destination: str,
        body: Mapping[str, object],
        send: ASGISend,
        holds: RequestHolds | None = None,
    ) -> None:
        """Collect the whole upstream, enforce it, render ONE OpenAI JSON (R8/R9).

        Reached only after :meth:`run_lifecycle` produced a proceed outcome. Delegates
        the drain + per-channel scan/resolve/enforce to ``collect_and_enforce`` (fail
        closed), then renders: a BLOCK ⇒ withhold + ``output_blocked`` (R8.3/R9.3); an
        uncomputable scan/decision ⇒ withhold + ``scan_failure`` (R8.4, raw bytes NEVER
        emitted, no ``fail_open_total`` increment); a clean enforce ⇒ ONE OpenAI JSON.
        Every reply leaves through ``emit_response`` so an unmapped value-code renders
        the declared generic envelope (R9.2). ``plan`` drives the ``Phase.OUTPUT``
        resolver + stamped mode; ``holds`` (fresh if ``None``) marks dispatch.
        """
        if holds is None:
            holds = RequestHolds()
        enforced = await collect_and_enforce(
            self._deps,
            destination,
            body,
            plan=plan,
            holds=holds,
            block_code=REFUSE_OUTPUT_BLOCKED,
            scan_failure_code=REFUSE_INPUT_SCAN_FAILURE,
        )
        if enforced.refuse_code is not None:
            await emit_response(send, errors.render_http(enforced.refuse_code))
            return
        json_body = self._deps.renderer.non_stream_body(req, enforced.deltas)
        # Stamp the Produced_Mode header from the plan's SELECTED mode (GW13 C5/R5).
        await emit_response(send, ok_response(json_body, plan.streaming_mode.value))
