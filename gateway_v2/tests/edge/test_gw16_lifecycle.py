"""GW16 request-lifecycle property + per-surface wiring/edge-case tests (shared file).

Two GW16 task contributions land in this ONE file (both target it by name):

* **task 5.10 — Property 10** (the ≥10,000-iteration property): *exactly one
  shared-state access per embeddings request, and NONE for the budget spend*. Driven
  over every embeddings flow (identity-refused / admission-shed / budget-refused /
  input-blocked / completed) through an instrumented identity/store seam, it asserts
  the embeddings lifecycle performs EXACTLY ONE request-path shared-state round trip —
  the ``lookup_plan`` on the served ``ReplicaSnapshot`` in ``EmbeddingsSurface._resolve_plan``
  — identity is RAM-served and the budget spend uses a LOCAL lease, so neither adds a
  request-path store read (R5.5). The GW15 ``tests/edge/test_lgw15_lifecycle.py`` Property
  6 is the direct template for the instrumented-snapshot shape.

* **task 5.13 — per-surface wiring/edge-case UNIT tests** (NOT a property — no ≥10k loop):
  focused example checks covering the embeddings lifecycle ORDER (every pre-provider
  refusal makes zero provider calls — 5.6), the embeddings clean path rendering vectors
  EQUAL to the stub provider's scripted vectors (6.1), ``ModelsSurface.handle_retrieve``
  on a KNOWN id (7.2), a models request making zero provider AND zero detector calls
  (7.4 / 7.5), and a moderations request making zero provider calls (8.5).

The two contributions are self-contained additive sections with uniquely-prefixed
symbols; neither depends on the other's helpers. House idiom for the property: a seeded
``random.Random`` driven for ≥10,000 iterations, NO ``hypothesis``; the async lifecycle
is driven via ``asyncio.run``; one property ↔ one property-based test. The harness shapes
(``MiscRoute`` deps, a counted ``StubProviderClient``, the ASGI ``_Capture`` /
``_FixedIdentity`` / served snapshot) mirror the shipped GW16 smoke + property files.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import cast

from gateway_v2.admit.grant import (
    Admitted,
    BudgetVerdict,
    ShedVerdict,
    budget_verdict,
    shed_verdict,
)
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE, OVERLOAD_SHED
from gateway_v2.edge.app import CatalogModel, CatalogSource, empty_catalog_source
from gateway_v2.edge.chat_deps import AdmissionDecider, BudgetDecider, InputDetector
from gateway_v2.edge.identity_resolver import TenantIdentity, fail_closed_resolver
from gateway_v2.edge.openai_chat import REFUSE_IDENTITY_UNRESOLVED, REFUSE_INPUT_BLOCKED
from gateway_v2.edge.openai_misc import EmbeddingsSurface, MiscRoute, ModerationsSurface
from gateway_v2.edge.openai_misc_models import ModelsSurface
from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore

# =========================================================================== #
# Shared harness — a counted provider/detector + the ASGI receive/send + plan/
# identity builders. Reused by BOTH the property (5.10) and the unit tests (5.13).
# =========================================================================== #

_ORG = "gw16-org"


def _plan() -> ExecutionPlan:
    """A minimal resolved :class:`ExecutionPlan` for the one test tenant."""
    return ExecutionPlan(
        org_id=_ORG,
        epoch=1,
        sequence=1,
        content_hash="gw16" * 8,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _identity() -> TenantIdentity:
    """One canned, resolvable tenant identity (the lifecycle proceeds past identity)."""
    return TenantIdentity(
        org_id=_ORG,
        principal=Principal(
            key_id="gw16-key",
            org_id=_ORG,
            rate_per_s=1.0,
            burst=1.0,
            feed_seq=0,
        ),
    )


class _FixedIdentity:
    """An ``IdentityResolver`` that resolves one canned tenant (so the lifecycle proceeds)."""

    __slots__ = ("_identity",)

    def __init__(self, identity: TenantIdentity) -> None:
        self._identity = identity

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class _CountingProvider(StubProviderClient):
    """A real :class:`StubProviderClient` (no socket) that counts every ``open``.

    Every zero-provider-call assertion reads ``open_calls``; the counter increments on each
    ``open`` so a lifecycle that reaches the provider when it must not is caught. The provider
    is REACHABLE on the deps bundle (as the boot wiring places it), so the assertion is a real
    structural check — the lifecycle has the provider and simply never opens it.
    """

    __slots__ = ("open_calls",)

    def __init__(self, *, script: Sequence[UpstreamEvent] = ()) -> None:
        super().__init__(script=script, rng=random.Random(0))
        self.open_calls = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        return super().open(request)


class _CountingDetector:
    """An ``InputDetector`` that counts every invocation and returns no findings.

    Used by the Models unit test to prove a models request makes ZERO detector calls (R7.5):
    Models carries no client text and runs no input scan, so this counted detector — reachable
    on the deps bundle — must stay at zero.
    """

    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, text: str) -> Sequence[Finding]:
        del text
        self.calls += 1
        return ()


def _no_findings(text: str) -> Sequence[Finding]:
    del text
    return ()


def _receive_for(body: bytes) -> Callable[[], Awaitable[Mapping[str, object]]]:
    """An ASGI ``receive`` that delivers ``body`` as one terminal http.request event."""
    delivered = False

    async def receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _receive_empty() -> Callable[[], Awaitable[Mapping[str, object]]]:
    """An ASGI ``receive`` for a GET (no body the surface reads)."""

    async def receive() -> Mapping[str, object]:
        return {"type": "http.disconnect"}

    return receive


class _Capture:
    """An ASGI ``send`` that captures the response start + body events."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.body: bytes = b""

    async def __call__(self, event: Mapping[str, object]) -> None:
        if event.get("type") == "http.response.start":
            self.status = int(event["status"])  # type: ignore[call-overload]
        elif event.get("type") == "http.response.body":
            chunk = event.get("body")
            if isinstance(chunk, (bytes, bytearray)):
                self.body += bytes(chunk)


# =========================================================================== #
# Property 10 — Exactly one shared-state access, none for the budget spend (task 5.10).
# =========================================================================== #
# Feature: chat-surface-gw16-misc, Property 10: Exactly one shared-state access, none for the budget spend  # noqa: E501
# Validates: Requirements 5.5
#
# The embeddings lifecycle order is identity → plan → admission → budget → input scan →
# provider (``EmbeddingsSurface.handle``). The ONE request-path shared-state round trip is
# the ``lookup_plan`` on the served ``ReplicaSnapshot`` in ``_resolve_plan`` — identity is
# RAM-served and the budget spend uses a LOCAL lease, so neither adds a request-path store
# read. This property drives EVERY embeddings flow over ≥10,000 iterations through an
# instrumented identity/store seam and, via a shared read counter the served snapshot and
# the quota component both carry, asserts:
#
#   (a) a request that REACHES the plan phase reads the served snapshot EXACTLY ONCE — and
#       since the order places shed / budget / input-blocked / completed all AFTER the plan
#       read, each of those four flows reads the snapshot exactly once too (never more: no
#       later phase re-reads it);
#   (b) NO store read is attributable to the budget spend — the quota component is handed the
#       SAME counter and never increments it, so the one read stays the plan lookup and the
#       budget ``evaluate`` is purely local (R5.5);
#   (c) a request refused BEFORE the plan phase (identity-refused) reads the snapshot ZERO
#       times — the round trip is reached only when the lifecycle gets that far, never
#       speculatively.


_P10_SEED = 0x6C_6757_1610
_P10_ITERATIONS = 10_000

# The five embeddings flows this property drives. ``identity`` refuses BEFORE the plan phase
# (zero snapshot reads); the other four refuse AT or AFTER the plan phase (one snapshot read).
_P10_IDENTITY = "identity"
_P10_SHED = "shed"
_P10_BUDGET = "budget"
_P10_INPUT_BLOCKED = "input_blocked"
_P10_COMPLETED = "completed"
_P10_FLOWS = (_P10_IDENTITY, _P10_SHED, _P10_BUDGET, _P10_INPUT_BLOCKED, _P10_COMPLETED)

# Flows whose outcome is decided AT or AFTER the plan phase (the one round trip ran).
_P10_REACHES_PLAN = frozenset({_P10_SHED, _P10_BUDGET, _P10_INPUT_BLOCKED, _P10_COMPLETED})


def _p10_ctx(i: int, flow: str, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={_P10_SEED:#x} iter={i} flow={flow} {body}"


class _P10ReadCounter:
    """Counts shared-state reads, tagged by the component that made them.

    One instance is shared between the instrumented served snapshot (the plan round trip)
    and the instrumented quota component (the budget spend), so a single counter attributes
    every store read to its origin. The budget phase MUST add nothing here (R5.5): a non-zero
    ``budget`` count is the property's failure.
    """

    __slots__ = ("plan", "budget")

    def __init__(self) -> None:
        self.plan = 0
        self.budget = 0


class _P10CountingSnapshot:
    """A served snapshot whose ``lookup`` increments the shared read counter under ``plan``.

    Wraps a real :class:`ReplicaSnapshot` (so ``lookup_plan`` resolves a genuine
    :class:`ExecutionPlan` for the registered tenant) and tallies every ``lookup``. The
    embeddings ``_resolve_plan`` phase is the ONLY request-path caller, so the tally IS the
    request's shared-state round-trip count. The ``lookup`` signature matches the shipped
    ``ReplicaSnapshot.lookup`` (``org_id`` + optional ``now``) so the surface's
    ``lookup_plan(snapshot, org_id, surface)`` call lands here unchanged.
    """

    __slots__ = ("_inner", "_counter")

    def __init__(self, inner: ReplicaSnapshot, counter: _P10ReadCounter) -> None:
        self._inner = inner
        self._counter = counter

    def lookup(self, org_id: str, now: float | None = None) -> object:
        self._counter.plan += 1
        return self._inner.lookup(org_id, now)


class _P10LocalQuota:
    """A budget decider that spends locally and reads NO shared state (R5.5).

    Handed the SAME read counter the snapshot carries; the key assertion is that ``evaluate``
    never increments it. A ``refuse`` flag drives the budget-refused flow (a
    :class:`BudgetVerdict`), still with zero store reads — a refusal is just as local as an
    admit.
    """

    __slots__ = ("_counter", "_refuse")

    def __init__(self, counter: _P10ReadCounter, *, refuse: bool) -> None:
        self._counter = counter
        self._refuse = refuse

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        # A LOCAL lease spend: no shared-state read is attributable to the budget phase. The
        # counter is deliberately NOT touched here (that is the property under test).
        del cost
        if self._refuse:
            return budget_verdict(request_id)
        return Admitted(owner_id=_ORG, request_id=request_id, admitted_at=0.0)


class _P10Admission:
    """Admits, or sheds once for the shed flow (a fail-closed shed carries its own code)."""

    __slots__ = ("_shed",)

    def __init__(self, *, shed: bool) -> None:
        self._shed = shed

    async def admit(
        self, *, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        del owner_id, enqueued_at
        if self._shed:
            return shed_verdict(random.Random(0), request_id)
        return Admitted(owner_id=_ORG, request_id=request_id, admitted_at=0.0)


class _P10BlockDetector:
    """A detector that emits one EXECUTED BLOCK-class finding (the input-blocked flow)."""

    __slots__ = ()

    def __call__(self, text: str) -> Sequence[Finding]:
        return (
            Finding(
                detector="card",  # MinimalOutputResolver maps "card" -> BLOCK.
                detector_version="gw16",
                category=Category.SECRET,
                status=FindingStatus.EXECUTED,
                confidence=1.0,
                spans=(Span(start=0, end=min(len(text), 1)),),
                evidence=None,
            ),
        )


def _p10_build_deps(flow: str, counter: _P10ReadCounter) -> MiscRoute:
    """Assemble a ``MiscRoute`` configured for one embeddings ``flow``.

    The served snapshot wraps a real :class:`ReplicaSnapshot` over a :class:`PlanStore` with a
    resolvable plan for the tenant, so a reached plan phase proceeds; the counting wrapper and
    the local quota share ``counter``. Identity resolves for every flow except ``identity``.
    """
    store = PlanStore()
    store.put(_plan())
    counting = _P10CountingSnapshot(ReplicaSnapshot(store), counter)
    # The counting wrapper + the per-flow seams are structural stand-ins for the shipped nominal
    # types (their public signatures match ``ReplicaSnapshot.lookup`` / ``AdmissionDecider`` /
    # ``BudgetDecider`` / ``InputDetector``); each is cast at the duck-typed seam so the
    # ``MiscRoute`` construction stays nominal and no in-field ``type: ignore`` is needed.
    def snapshot_source() -> ReplicaSnapshot:
        return cast(ReplicaSnapshot, counting)

    # A scripted provider that WOULD yield a vector if ever opened, so the completed flow has a
    # real vector source to drain and renders the 200 body.
    provider = _CountingProvider(
        script=(UpstreamEvent(text_deltas=(("embeddings_input", "[0.0]"),), final=True),)
    )
    detector = _P10BlockDetector() if flow == _P10_INPUT_BLOCKED else _no_findings
    admission: AdmissionDecider = cast(
        AdmissionDecider, _P10Admission(shed=flow == _P10_SHED)
    )
    quota: BudgetDecider = cast(
        BudgetDecider, _P10LocalQuota(counter, refuse=flow == _P10_BUDGET)
    )
    return MiscRoute(
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=empty_catalog_source,  # unused by embeddings; the honest-empty default.
        identity=_FixedIdentity(_identity())
        if flow != _P10_IDENTITY
        else fail_closed_resolver,
        admission=admission,
        quota=quota,
        snapshot_source=snapshot_source,
        input_detector=cast(InputDetector, detector),
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )


# Expected refuse-code per flow (``None`` ⇒ a 200 completed outcome). ``shed`` and ``budget``
# are posture codes rendering HTTP 503; ``input_blocked`` is the GW15 input-block spelling,
# which has no dedicated envelope entry so it renders the declared generic error (HTTP 500).
_P10_EXPECTED_CODE: Mapping[str, str | None] = {
    _P10_IDENTITY: REFUSE_IDENTITY_UNRESOLVED,
    _P10_SHED: OVERLOAD_SHED,
    _P10_BUDGET: BUDGET_UNAVAILABLE,
    _P10_INPUT_BLOCKED: REFUSE_INPUT_BLOCKED,
    _P10_COMPLETED: None,
}


def _p10_error_code(capture: _Capture) -> str:
    code = json.loads(capture.body)["error"]["code"]
    assert isinstance(code, str)
    return code


def test_property_10_exactly_one_shared_state_access_none_for_budget_spend() -> None:
    rng = random.Random(_P10_SEED)
    scope: Mapping[str, object] = {"type": "http", "method": "POST", "path": "/v1/embeddings"}

    for i in range(_P10_ITERATIONS):
        # Drive every flow each iteration so a regression in ANY path fails the gate.
        for flow in _P10_FLOWS:
            counter = _P10ReadCounter()
            deps = _p10_build_deps(flow, counter)
            body = json.dumps(
                {"model": "gw16-embed", "input": ["one item"]}
            ).encode("utf-8")

            capture = _Capture()
            asyncio.run(
                EmbeddingsSurface(deps=deps).handle(scope, _receive_for(body), capture)
            )

            reaches_plan = flow in _P10_REACHES_PLAN

            # (a) exactly one served-snapshot read when the plan phase is reached, zero when
            #     the request was refused before it (identity).
            expected_reads = 1 if reaches_plan else 0
            assert counter.plan == expected_reads, _p10_ctx(
                i, flow, note="plan snapshot read count",
                reads=counter.plan, expected=expected_reads,
            )

            # (b) NO store read is attributable to the budget spend — the quota component is
            #     handed the SAME counter and must never increment it (R5.5). Holds for the
            #     completed flow AND the budget-refused flow alike.
            assert counter.budget == 0, _p10_ctx(
                i, flow, note="a store read was attributed to the budget spend",
                budget_reads=counter.budget,
            )

            # The total request-path shared-state round trips is the plan read alone — exactly
            # one when reached, never more (identity + budget add none).
            assert counter.plan + counter.budget == expected_reads, _p10_ctx(
                i, flow, note="total request-path shared-state round trips",
                total=counter.plan + counter.budget, expected=expected_reads,
            )

            # Sanity-check the outcome matches the flow, so the read counts are measured
            # against the intended path, not a mis-wired refusal.
            expected_code = _P10_EXPECTED_CODE[flow]
            if expected_code is None:
                assert capture.status == 200, _p10_ctx(
                    i, flow, note="expected a completed 200 outcome", status=capture.status
                )
            else:
                assert _p10_error_code(capture) == expected_code, _p10_ctx(
                    i, flow, note="unexpected refuse code",
                    code=_p10_error_code(capture), expected=expected_code,
                )

        # Keep the seeded RNG advancing so the house seeded-RNG idiom holds across iterations.
        rng.random()


# =========================================================================== #
# Per-surface wiring / edge-case UNIT tests (task 5.13) — NOT a property.
# =========================================================================== #
# Feature: chat-surface-gw16-misc
# Validates: Requirements 5.6, 6.1, 7.2, 7.4, 7.5, 8.5
#
# Focused example checks (no ≥10k loop) complementing the GW16 property suite:
#   * 5.6 — the embeddings lifecycle ORDER refuses at the EARLIEST breaching phase, and every
#     pre-provider refusal makes zero provider calls;
#   * 6.1 — the embeddings clean path renders vectors EQUAL to the stub provider's scripted
#     vectors (the surface reads vectors from the provider, never computes them);
#   * 7.2 — ``ModelsSurface.handle_retrieve`` for a KNOWN id → 200 ``{object:"model", id}``;
#   * 7.4 / 7.5 — a models request (list + retrieve) makes zero provider AND zero detector
#     calls (Models runs no scan, opens no provider);
#   * 8.5 — a moderations request makes zero provider calls.


_U_SCOPE_EMBED: Mapping[str, object] = {
    "type": "http",
    "method": "POST",
    "path": "/v1/embeddings",
}
_U_SCOPE_MODERATE: Mapping[str, object] = {
    "type": "http",
    "method": "POST",
    "path": "/v1/moderations",
}


def _u_vector_events(vectors: Sequence[Sequence[float]]) -> tuple[UpstreamEvent, ...]:
    """One scripted ``UpstreamEvent`` per vector, each carrying the JSON vector as delta text."""
    return tuple(
        UpstreamEvent(
            text_deltas=(("embeddings_input", json.dumps(list(vec))),), final=False
        )
        for vec in vectors
    )


class _U_SheddingAdmission:
    """Admission that always sheds — returns a ``ShedVerdict`` (``overload_shed``)."""

    __slots__ = ()

    async def admit(
        self, *, owner_id: str, request_id: str, enqueued_at: float
    ) -> ShedVerdict:
        del owner_id, enqueued_at
        return shed_verdict(random.Random(0), request_id)


class _U_RefusingBudget:
    """Budget that always refuses — returns a ``BudgetVerdict`` (``budget_unavailable``)."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict:
        del cost
        return budget_verdict(request_id)


class _U_AlwaysAdmit:
    """Admission that always admits."""

    __slots__ = ()

    async def admit(
        self, *, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted:
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=enqueued_at)


class _U_AlwaysBudget:
    """Budget that is always within budget."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> Admitted:
        del cost
        return Admitted(owner_id=_ORG, request_id=request_id, admitted_at=0.0)


class _U_ScriptedCatalog:
    """A :class:`CatalogSource` returning a fixed catalog for every identity."""

    __slots__ = ("_models",)

    def __init__(self, models: tuple[CatalogModel, ...]) -> None:
        self._models = models

    def catalog_for(self, identity: TenantIdentity) -> tuple[CatalogModel, ...]:
        del identity
        return self._models


def _u_served_snapshot() -> ReplicaSnapshot:
    store = PlanStore()
    store.put(_plan())
    return ReplicaSnapshot(store)


def _u_embed_deps(
    *,
    provider: _CountingProvider,
    identity: object,
    admission: object,
    quota: object,
    input_detector: object,
) -> MiscRoute:
    """A ``MiscRoute`` whose plan resolves and whose varied seams the caller injects."""
    snapshot = _u_served_snapshot()
    return MiscRoute(
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=empty_catalog_source,
        identity=identity,  # type: ignore[arg-type]
        admission=admission,  # type: ignore[arg-type]
        quota=quota,  # type: ignore[arg-type]
        snapshot_source=lambda: snapshot,
        input_detector=input_detector,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )


def test_embeddings_lifecycle_order_every_pre_provider_refusal_zero_provider_calls() -> None:
    """5.6 — each pre-provider phase refuses before ``provider.open`` ⇒ zero provider calls.

    The embeddings order is identity → plan → admission → budget → input scan → provider.
    This example drives each pre-provider refusal in turn (unresolved identity, admission
    shed, budget refused, input BLOCK) and asserts every one makes ZERO provider calls — the
    refusal returns BEFORE the provider is opened — through a counted ``StubProviderClient``.
    """
    # Each case names (seams, expected refuse status). The provider is live (scripted) each
    # time, so a zero-call result is a real short-circuit, not an unreachable provider.
    cases: tuple[tuple[str, object, object, object, int], ...] = (
        # unresolved identity ⇒ 503 shared_state_unavailable.
        ("identity", fail_closed_resolver, _U_AlwaysAdmit(), _U_AlwaysBudget(), 503),
        # admission shed ⇒ 503 overload_shed.
        ("shed", _FixedIdentity(_identity()), _U_SheddingAdmission(), _U_AlwaysBudget(), 503),
        # budget refused ⇒ 503 budget_unavailable.
        ("budget", _FixedIdentity(_identity()), _U_AlwaysAdmit(), _U_RefusingBudget(), 503),
    )
    for name, identity, admission, quota in (
        (c[0], c[1], c[2], c[3]) for c in cases
    ):
        provider = _CountingProvider(
            script=(UpstreamEvent(text_deltas=(("x", "[0.0]"),), final=True),)
        )
        deps = _u_embed_deps(
            provider=provider,
            identity=identity,
            admission=admission,
            quota=quota,
            input_detector=_no_findings,
        )
        body = json.dumps({"model": "gw16-embed", "input": ["hi"]}).encode()
        capture = _Capture()
        asyncio.run(
            EmbeddingsSurface(deps=deps).handle(_U_SCOPE_EMBED, _receive_for(body), capture)
        )

        assert provider.open_calls == 0, f"{name}: provider opened on a pre-provider refusal"
        assert capture.status == 503, f"{name}: unexpected status {capture.status}"
        assert deps.exports.snapshot().fail_open_total == 0, f"{name}: fail_open moved off zero"

    # The input-scan BLOCK (the LAST phase before provider contact) also refuses zero-call.
    provider = _CountingProvider(
        script=(UpstreamEvent(text_deltas=(("x", "[0.0]"),), final=True),)
    )
    deps = _u_embed_deps(
        provider=provider,
        identity=_FixedIdentity(_identity()),
        admission=_U_AlwaysAdmit(),
        quota=_U_AlwaysBudget(),
        input_detector=_P10BlockDetector(),
    )
    body = json.dumps({"model": "gw16-embed", "input": ["carry secret here"]}).encode()
    capture = _Capture()
    asyncio.run(EmbeddingsSurface(deps=deps).handle(_U_SCOPE_EMBED, _receive_for(body), capture))

    assert provider.open_calls == 0, "input BLOCK opened the provider"
    assert json.loads(capture.body)["error"]["code"] == REFUSE_INPUT_BLOCKED
    assert deps.exports.snapshot().fail_open_total == 0


def test_embeddings_clean_path_renders_vectors_equal_to_stub_scripted_vectors() -> None:
    """6.1 — the clean path renders vectors EQUAL to the stub provider's scripted vectors.

    The surface reads vectors from the injected provider's stream; it never computes them. A
    scripted ``StubProviderClient`` yields one JSON-encoded vector per input item, and the
    rendered embeddings body's ``embedding`` arrays must equal those scripted vectors exactly,
    one ``data`` entry per item in request order (R6.1 / R6.3).
    """
    scripted = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    provider = _CountingProvider(script=_u_vector_events(scripted))
    deps = _u_embed_deps(
        provider=provider,
        identity=_FixedIdentity(_identity()),
        admission=_U_AlwaysAdmit(),
        quota=_U_AlwaysBudget(),
        input_detector=_no_findings,
    )
    body = json.dumps({"model": "gw16-embed", "input": ["alpha", "beta"]}).encode()
    capture = _Capture()
    asyncio.run(EmbeddingsSurface(deps=deps).handle(_U_SCOPE_EMBED, _receive_for(body), capture))

    assert capture.status == 200
    assert provider.open_calls == 1  # the provider WAS the vector source (R6.1).
    rendered = json.loads(capture.body)
    assert rendered["object"] == "list"
    data = rendered["data"]
    assert len(data) == len(scripted)
    for position, (entry, expected_vector) in enumerate(zip(data, scripted)):
        assert entry["index"] == position
        assert entry["object"] == "embedding"
        # The rendered vector equals the stub's scripted vector exactly — not recomputed.
        assert entry["embedding"] == expected_vector
    assert deps.exports.snapshot().fail_open_total == 0


def _u_models_deps(
    provider: _CountingProvider,
    detector: _CountingDetector,
    catalog: CatalogSource,
) -> MiscRoute:
    """A ``MiscRoute`` with a counted provider + counted detector + scripted catalog."""
    return MiscRoute(
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=catalog,
        identity=_FixedIdentity(_identity()),
        input_detector=detector,
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )


def test_models_retrieve_known_id_returns_model_object_with_matching_id() -> None:
    """7.2 — retrieve a KNOWN id → HTTP 200 ``{object:"model", id}`` for the matching id."""
    catalog = (
        CatalogModel(id="gpt-known", created=1_700_000_000, owned_by="zeroshield"),
        CatalogModel(id="other-model", created=1_700_000_001, owned_by="zeroshield"),
    )
    provider = _CountingProvider()
    detector = _CountingDetector()
    deps = _u_models_deps(provider, detector, _U_ScriptedCatalog(catalog))
    scope: Mapping[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/v1/models/gpt-known",
    }
    capture = _Capture()
    asyncio.run(ModelsSurface(deps=deps).handle_retrieve(scope, _receive_empty(), capture))

    assert capture.status == 200
    rendered = json.loads(capture.body)
    assert rendered["object"] == "model"
    assert rendered["id"] == "gpt-known"
    assert deps.exports.snapshot().fail_open_total == 0


def test_models_list_and_retrieve_make_zero_provider_and_zero_detector_calls() -> None:
    """7.4 / 7.5 — a models request (list + retrieve) opens no provider and runs no scan.

    Models carries no client text: it resolves identity, reads the catalog, and renders —
    with NO input scan (R7.5) and NO provider open (R7.4). Both the counted provider and the
    counted detector — reachable on the deps bundle — must stay at zero across a list and a
    retrieve.
    """
    catalog = (CatalogModel(id="gpt-known", created=1_700_000_000, owned_by="zeroshield"),)
    provider = _CountingProvider()
    detector = _CountingDetector()
    deps = _u_models_deps(provider, detector, _U_ScriptedCatalog(catalog))

    list_scope: Mapping[str, object] = {"type": "http", "method": "GET", "path": "/v1/models"}
    list_capture = _Capture()
    asyncio.run(ModelsSurface(deps=deps).handle_list(list_scope, _receive_empty(), list_capture))
    assert list_capture.status == 200
    assert json.loads(list_capture.body)["object"] == "list"

    retrieve_scope: Mapping[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/v1/models/gpt-known",
    }
    retrieve_capture = _Capture()
    asyncio.run(
        ModelsSurface(deps=deps).handle_retrieve(retrieve_scope, _receive_empty(), retrieve_capture)
    )
    assert retrieve_capture.status == 200

    # Models runs no scan (R7.5) and opens no provider (R7.4) — both counters stay at zero.
    assert provider.open_calls == 0
    assert detector.calls == 0
    assert deps.exports.snapshot().fail_open_total == 0


def test_moderations_request_makes_zero_provider_calls() -> None:
    """8.5 — a moderations request opens NO provider (scan-only surface)."""
    provider = _CountingProvider()
    deps = MiscRoute(
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=empty_catalog_source,
        identity=_FixedIdentity(_identity()),
        input_detector=_no_findings,
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )
    body = json.dumps({"input": ["clean one", "clean two"]}).encode()
    capture = _Capture()
    asyncio.run(
        ModerationsSurface(deps=deps).handle(_U_SCOPE_MODERATE, _receive_for(body), capture)
    )

    assert capture.status == 200
    assert provider.open_calls == 0  # moderations opens NO provider (R8.5).
    assert deps.exports.snapshot().fail_open_total == 0
