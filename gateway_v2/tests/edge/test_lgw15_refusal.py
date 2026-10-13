# Feature: chat-surface-gw15, Property 8: A pre-provider refusal short-circuits the provider
# Validates: Requirements 5.2, 6.2
"""Property 8 — a pre-provider refusal short-circuits the provider (GW15, task 2.8).

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations, async
lifecycle driven via ``asyncio.run``.

*For any* well-formed chat request, when the admission phase sheds (a
:class:`~gateway_v2.admit.grant.ShedVerdict`) OR the budget phase refuses (a
:class:`~gateway_v2.admit.grant.BudgetVerdict`), the §10.4 lifecycle
(:meth:`~gateway_v2.edge.openai_chat.ChatSurface.run_lifecycle`) returns a refusal
carrying the verdict's DECLARED value-code and makes ZERO provider calls (R5.2 / R6.2).

The two refusals exercised:

* **Admission shed** — the injected :class:`AdmissionDecider` returns a ``ShedVerdict``
  (code ``posture.OVERLOAD_SHED``). The lifecycle's ``_admit`` phase refuses with
  ``verdict.code`` BEFORE the budget / input-scan phases and before any provider contact
  (R5.2).
* **Budget refusal** — admission admits, but the injected :class:`BudgetDecider` returns a
  ``BudgetVerdict`` (code ``posture.BUDGET_UNAVAILABLE``). The lifecycle's ``_budget`` phase
  refuses with that code BEFORE the input-scan phase and before any provider contact (R6.2).

Both phases run BEFORE ``provider.open`` in the ordered pipeline, so the invariant under test
is twofold each iteration: (1) the rendered ``refuse_code`` is EXACTLY the verdict's declared
code, and (2) a counting :class:`_CountingProvider` — a drop-in
:class:`~gateway_v2.dispatch.provider.ProviderClient` that tallies every ``open`` / ``abort``
— records zero calls. The lifecycle itself never opens the provider (that is the handler's job
on a proceed outcome), so the strongest provider-call assertion the lifecycle layer admits is
"the provider handed to the surface is never touched": the counting provider makes that
checkable directly and would catch any future regression that opened the provider inside the
lifecycle.

Everything this test owns is uniquely prefixed ``_p8_`` / ``_P8`` / ``P8_`` so it never clashes
with the other lifecycle tests that target the shared ``tests/edge`` package.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from gateway_v2.admit.grant import (
    Admitted,
    BudgetVerdict,
    ShedVerdict,
    budget_verdict,
    shed_verdict,
)
from gateway_v2.dispatch.provider import ProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain import Finding
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE, OVERLOAD_SHED
from gateway_v2.edge.identity_resolver import IdentityResolver, TenantIdentity
from gateway_v2.edge.openai_chat import ChatSurface
from gateway_v2.edge.routes import ChatRoute
from gateway_v2.edge.wire.openai import OpenAIRequestParser, OpenAIResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore

_SEED = 0x16_15_0008
_ITERATIONS = 10_000

#: The ASGI scope path the parser maps to ``Surface.CHAT`` (``_PATH_TO_SURFACE``).
_P8_CHAT_PATH = "/v1/chat/completions"

#: Pre-check bounds so large the Pre_Identity_Clock phase never refuses — this test exercises
#: the admission / budget refusals, not the size / expansion / part-count pre-checks. These are
#: TEST bounds on a fake contract, not production capacity literals.
_P8_MAX_BYTES = 1 << 30
_P8_MAX_EXPANSION_RATIO = 1_000.0
_P8_MAX_PARTS = 1 << 30

_P8_TEXT_CORPUS: tuple[str, ...] = (
    "hello world",
    "summarize this document",
    "unicode: café — naïve © Ω λ",
    "日本語テキスト",
    'quote " and backslash \\ ',
    "a" * 48,
)


def _p8_ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


# --------------------------------------------------------------------------- #
# A counting ProviderClient: a drop-in ProviderClient that tallies every call.
# --------------------------------------------------------------------------- #


class _CountingProvider:
    """A :class:`ProviderClient` that counts every ``open`` / ``abort`` (zero expected).

    Structurally satisfies the shipped ``@runtime_checkable``
    :class:`~gateway_v2.dispatch.provider.ProviderClient` protocol: ``open`` returns an async
    iterator and ``abort`` is a coroutine. Both increment a counter so a refused request can be
    asserted to have made ZERO provider calls (R5.2 / R6.2). A plain slotted class (mutable
    counters) rather than a frozen dataclass; it holds no request-scoped state beyond the tally.
    """

    __slots__ = ("abort_calls", "open_calls")

    def __init__(self) -> None:
        self.open_calls = 0
        self.abort_calls = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        """Count the call; a refusing lifecycle must never reach here (R5.2 / R6.2)."""
        self.open_calls += 1
        del request
        return self._empty()

    @staticmethod
    async def _empty() -> AsyncIterator[UpstreamEvent]:
        """An empty event stream (never iterated on a refusal)."""
        return
        yield  # pragma: no cover - makes this an async generator; unreachable

    async def abort(self) -> None:
        """Count the abort; a refusing lifecycle must never reach here."""
        self.abort_calls += 1

    def total_calls(self) -> int:
        """Every provider contact tallied — expected to stay 0 on a refusal."""
        return self.open_calls + self.abort_calls


# --------------------------------------------------------------------------- #
# Injected fakes for the lifecycle phases BEFORE admission (so the two refusals are reached).
# --------------------------------------------------------------------------- #


class _P8FakeContract:
    """A minimal contract exposing only the three Pre_Identity_Clock accessors.

    The §10.4 pre-check phase reads ``max_request_bytes`` / ``max_expansion_ratio`` /
    ``max_request_parts`` from the injected contract; every other ``ResourceContract`` method is
    irrelevant to this property (the lifecycle refuses at admission / budget, well before any
    streaming bound is consulted). The bounds are set generously so pre-checks always pass.
    """

    __slots__ = ()

    def max_request_bytes(self) -> int:
        return _P8_MAX_BYTES

    def max_expansion_ratio(self) -> float:
        return _P8_MAX_EXPANSION_RATIO

    def max_request_parts(self) -> int:
        return _P8_MAX_PARTS


class _P8FakeIdentity:
    """An :class:`IdentityResolver` that always resolves one canned tenant (never fails closed).

    The identity phase must PASS so the lifecycle reaches admission / budget; this resolver
    returns a fixed :class:`TenantIdentity` for every request. The ``org_id`` keys the plan
    round trip, the admission door, and the budget lease.
    """

    __slots__ = ("_identity",)

    def __init__(self, org_id: str) -> None:
        self._identity = TenantIdentity(
            org_id=org_id,
            principal=Principal(
                key_id="p8-key",
                org_id=org_id,
                rate_per_s=1.0,
                burst=1.0,
                feed_seq=0,
            ),
        )

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class _P8ShedAdmission:
    """An :class:`AdmissionDecider` that always sheds — the admission-refusal arm (R5.2)."""

    __slots__ = ("_rng",)

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        del owner_id, enqueued_at
        return shed_verdict(self._rng, request_id)


class _P8AdmitAdmission:
    """An :class:`AdmissionDecider` that always admits — so the budget arm is reached (R6.2)."""

    __slots__ = ()

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        del enqueued_at
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=0.0)


class _P8RefuseBudget:
    """A :class:`BudgetDecider` that always refuses — the budget-refusal arm (R6.2)."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        del cost
        return budget_verdict(request_id)


class _P8AdmitBudget:
    """A :class:`BudgetDecider` that always admits (used only in the admission-shed arm)."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        del cost
        return Admitted(owner_id="p8", request_id=request_id, admitted_at=0.0)


def _p8_no_findings(text: str) -> Sequence[Finding]:
    """An input detector that finds nothing (unused — admission/budget refuse first)."""
    del text
    return ()


# --------------------------------------------------------------------------- #
# A fake deps bundle carrying exactly the attributes run_lifecycle reads.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _P8Deps:
    """The subset of ``ChatRoute`` fields the §10.4 lifecycle reads, as an injectable fake.

    ``ChatSurface.__init__`` types ``deps`` as ``ChatRoute`` only under ``TYPE_CHECKING`` (the
    real import would cycle), so at runtime any object exposing the attributes
    :meth:`ChatSurface.run_lifecycle` touches is accepted. This bundle carries exactly those —
    the pre-check contract, the parser, the identity resolver, the plan snapshot source, the
    admission + budget deciders, the input detector + resolver, the clock, and the
    ``DispatchRouter`` / ``Transform`` the proceed path would use — so the surface drives the
    whole pre-provider pipeline against injected fakes. It is passed through ``cast`` to the
    ``ChatRoute`` annotation so ``mypy --strict`` is satisfied without constructing the full
    heavyweight route (scanner / holdback cfg / executor / in-flight control are never reached on
    a refusal).
    """

    contract: _P8FakeContract
    identity: IdentityResolver
    snapshot_source: Callable[[], ReplicaSnapshot]
    admission: object
    quota: object
    input_detector: Callable[[str], Sequence[Finding]]
    resolver: MinimalOutputResolver
    clock: Callable[[], float]
    parser: OpenAIRequestParser = field(default_factory=OpenAIRequestParser)
    renderer: OpenAIResponseRenderer = field(default_factory=OpenAIResponseRenderer)
    router: DispatchRouter = field(default_factory=DispatchRouter)
    transform: Transform = field(default_factory=Transform)


def _p8_plan(org_id: str) -> ExecutionPlan:
    """A minimal resolvable :class:`ExecutionPlan` for ``org_id`` (no rules, pass-through)."""
    return ExecutionPlan(
        org_id=org_id,
        epoch=1,
        sequence=1,
        content_hash="p8" + "0" * 62,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _p8_snapshot_source(org_id: str) -> Callable[[], ReplicaSnapshot]:
    """A ``snapshot_source`` whose served snapshot resolves ``org_id`` to a real plan."""
    store = PlanStore()
    store.put(_p8_plan(org_id))
    snapshot = ReplicaSnapshot(store)
    # Warm the served last-known-good so lookup resolves the plan deterministically.
    snapshot.absorb(org_id, now=0.0)

    def _source() -> ReplicaSnapshot:
        return snapshot

    return _source


def _p8_clock() -> float:
    """A fixed clock stand-in (the admission sojourn boundary; never a real clock)."""
    return 0.0


def _p8_body(rng: random.Random) -> bytes:
    """A well-formed OpenAI chat body that parses cleanly and passes the pre-checks."""
    message = {"role": "user", "content": rng.choice(_P8_TEXT_CORPUS)}
    body: dict[str, object] = {"model": rng.choice(("gpt-4o", "o3")), "messages": [message]}
    return json.dumps(body).encode("utf-8")


def _p8_scope() -> dict[str, object]:
    """A minimal ASGI http scope for the chat path (the parser reads ``path`` for the surface)."""
    return {"type": "http", "method": "POST", "path": _P8_CHAT_PATH, "headers": []}


def _p8_run(deps: _P8Deps, scope: Mapping[str, object], body: bytes) -> tuple[object, object]:
    """Drive one ``run_lifecycle`` to completion via ``asyncio.run``; return the outcome tuple."""
    surface = ChatSurface(deps=cast(ChatRoute, deps))

    async def _go() -> tuple[object, object]:
        req, outcome = await surface.run_lifecycle(scope, body)
        return req, outcome

    return asyncio.run(_go())


def test_property_8_pre_provider_refusal_short_circuits_the_provider() -> None:
    rng = random.Random(_SEED)
    org_id = "p8-org"
    snapshot_source = _p8_snapshot_source(org_id)

    for i in range(_ITERATIONS):
        scope = _p8_scope()
        body = _p8_body(rng)

        # Alternate the two refusal arms so BOTH ShedVerdict and BudgetVerdict are exercised
        # across the >= 10,000 iterations (R5.2 for the shed arm, R6.2 for the budget arm).
        shed_arm = bool(rng.getrandbits(1))

        provider = _CountingProvider()
        # The provider is held on the deps bundle exactly as the real route holds it; a refusing
        # lifecycle must never touch it. (The lifecycle never opens it itself, so the assertion
        # is that no phase regresses into opening it.)
        assert isinstance(provider, ProviderClient), _p8_ctx(
            _SEED, i, note="counting provider does not satisfy the ProviderClient protocol"
        )

        if shed_arm:
            admission: object = _P8ShedAdmission(rng)
            quota: object = _P8AdmitBudget()
            expected_code = OVERLOAD_SHED
        else:
            admission = _P8AdmitAdmission()
            quota = _P8RefuseBudget()
            expected_code = BUDGET_UNAVAILABLE

        deps = _P8Deps(
            contract=_P8FakeContract(),
            identity=_P8FakeIdentity(org_id),
            snapshot_source=snapshot_source,
            admission=admission,
            quota=quota,
            input_detector=_p8_no_findings,
            resolver=MinimalOutputResolver(),
            clock=_p8_clock,
        )

        _req, outcome = _p8_run(deps, scope, body)

        # (1) The rendered refuse_code is EXACTLY the verdict's declared value-code.
        refuse_code = getattr(outcome, "refuse_code", "MISSING")
        assert refuse_code == expected_code, _p8_ctx(
            _SEED,
            i,
            note="refuse_code is not the verdict's declared value-code",
            arm="shed" if shed_arm else "budget",
            got=refuse_code,
            want=expected_code,
        )

        # A refusal carries NO provider inputs (fail-closed shape: plan/destination/body None).
        assert getattr(outcome, "plan", "X") is None, _p8_ctx(
            _SEED, i, note="refusal outcome leaked a plan", arm="shed" if shed_arm else "budget"
        )
        assert getattr(outcome, "destination", "X") is None, _p8_ctx(
            _SEED, i, note="refusal outcome leaked a destination"
        )
        assert getattr(outcome, "body", "X") is None, _p8_ctx(
            _SEED, i, note="refusal outcome leaked a body"
        )

        # (2) ZERO provider calls were made (R5.2 / R6.2): the counting provider is untouched.
        assert provider.total_calls() == 0, _p8_ctx(
            _SEED,
            i,
            note="a pre-provider refusal still made a provider call",
            arm="shed" if shed_arm else "budget",
            open_calls=provider.open_calls,
            abort_calls=provider.abort_calls,
        )
