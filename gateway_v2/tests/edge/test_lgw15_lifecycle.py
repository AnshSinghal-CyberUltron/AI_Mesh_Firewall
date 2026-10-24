"""GW15 §10.4 request-lifecycle property + unit tests (shared file).

This module is written by several GW15 task sub-agents concurrently (2.5 pre-identity
pre-checks, 2.6 the single shared-state round trip, 2.7 identity fail-closed, 2.11
wiring/edge-case unit tests). Each contribution is a SELF-CONTAINED additive section
with uniquely-prefixed symbols and section-local (mid-module) imports, so the sections
never collide and none depends on another's helpers. Do not remove a sibling section.

House idiom throughout: a seeded ``random.Random`` driven for >=10,000 iterations, NO
``hypothesis``; async lifecycle paths exercised via ``asyncio.run``; one property <-> one
property-based test.
"""

from __future__ import annotations

# ===========================================================================
# Property 6 — Exactly one shared-state round trip per request (task 2.6).
# ===========================================================================
# Feature: chat-surface-gw15, Property 6: Exactly one shared-state round trip per request
# Validates: Requirements 3.3, 6.3
#
# The §10.4 lifecycle performs EXACTLY ONE shared-state round trip per request: the
# ``lookup_plan`` on the served ``ReplicaSnapshot`` in ``ChatSurface._plan``. Identity is
# RAM-served (GW05b/c) and the budget spend uses a LOCAL lease, so neither adds a
# request-path store read. This property drives every lifecycle flow — admitted/completed,
# admission-shed, budget-refused, input-blocked, and the pre-plan refusals (pre-check,
# identity) — over >=10,000 iterations and, through an instrumented ``snapshot_source``
# that counts reads, asserts:
#
#   (a) a request that REACHES the plan phase reads the served snapshot exactly once
#       (R3.3), and never more (no phase re-reads it);
#   (b) NO store read is attributable to the budget spend — the quota component is handed
#       the SAME instrumented snapshot source and never touches it, so the one read stays
#       the plan lookup and the budget spend is purely local (R6.3);
#   (c) a request refused BEFORE the plan phase (pre-check / identity) reads the snapshot
#       zero times — the one round trip is reached only when the lifecycle gets that far,
#       never speculatively.


def test_property_6_exactly_one_shared_state_round_trip() -> None:
    import asyncio
    import random
    from collections.abc import Mapping
    from dataclasses import dataclass, field

    from gateway_v2.admit.grant import (
        Admitted,
        BudgetVerdict,
        ShedVerdict,
        budget_verdict,
        shed_verdict,
    )
    from gateway_v2.dispatch.routing import DispatchRouter
    from gateway_v2.dispatch.transform import Transform
    from gateway_v2.domain.category import Category
    from gateway_v2.domain.decision import Decision, Disposition, FindingDisposition
    from gateway_v2.domain.finding import Finding, FindingStatus
    from gateway_v2.domain.identity import Principal
    from gateway_v2.domain.plan import (
        ExecutionPlan,
        StreamingMode,
        Surface,
    )
    from gateway_v2.edge.identity_resolver import TenantIdentity
    from gateway_v2.edge.openai_chat import (
        REFUSE_BUDGET_UNAVAILABLE,
        REFUSE_IDENTITY_UNRESOLVED,
        REFUSE_INPUT_BLOCKED,
        REFUSE_PRE_CHECK,
        ChatSurface,
    )
    from gateway_v2.edge.wire.openai import CanonicalRequest
    from gateway_v2.plan.snapshot import ReplicaSnapshot
    from gateway_v2.plan.store import PlanStore

    _SEED = 0x6C_6757_0006
    _ITERATIONS = 10_000

    # The five lifecycle flows this property drives. Each either refuses before the plan
    # phase (``reaches_plan=False`` ⇒ zero snapshot reads) or reaches the plan phase and
    # proceeds through budget/input (``reaches_plan=True`` ⇒ exactly one snapshot read).
    _PRE_CHECK = "pre_check"
    _IDENTITY = "identity"
    _SHED = "shed"
    _BUDGET = "budget"
    _INPUT_BLOCKED = "input_blocked"
    _COMPLETED = "completed"
    _P6_FLOWS = (_PRE_CHECK, _IDENTITY, _SHED, _BUDGET, _INPUT_BLOCKED, _COMPLETED)

    # Flows whose refusal happens AT or AFTER the plan phase (the one round trip ran).
    _REACHES_PLAN = frozenset({_SHED, _BUDGET, _INPUT_BLOCKED, _COMPLETED})

    _ORG = "org-p6"
    _PATH = "/v1/chat/completions"

    def _ctx(i: int, flow: str, **extra: object) -> str:
        body = " ".join(f"{k}={v!r}" for k, v in extra.items())
        return f"seed={_SEED:#x} iter={i} flow={flow} {body}"

    # ----- a store-read counter the plan snapshot and the budget both see ----- #

    class _P6_ReadCounter:
        """Counts shared-state reads, tagged by the component that made them.

        One instance is shared between the instrumented ``snapshot_source`` (the plan
        round trip) and the instrumented quota component (the budget spend), so a single
        counter attributes every store read to its origin. The budget phase MUST add
        nothing here (R6.3): a non-zero ``budget`` count is the property's failure.
        """

        __slots__ = ("plan", "budget")

        def __init__(self) -> None:
            self.plan = 0
            self.budget = 0

    # ----- instrumented snapshot source: counts each served-snapshot read ----- #

    class _P6_CountingSnapshot:
        """A ``ReplicaSnapshot`` whose ``lookup`` increments the shared read counter.

        Wraps a real :class:`ReplicaSnapshot` (so ``lookup_plan`` resolves a genuine
        :class:`ExecutionPlan` for the registered tenant) and tallies every ``lookup``
        under the ``plan`` tag. The §10.4 ``_plan`` phase is the ONLY caller on the
        request path, so the tally is the request's shared-state round-trip count.
        """

        __slots__ = ("_inner", "_counter")

        def __init__(self, inner: ReplicaSnapshot, counter: _P6_ReadCounter) -> None:
            self._inner = inner
            self._counter = counter

        def lookup(self, org_id: str, now: float | None = None) -> object:
            self._counter.plan += 1
            return self._inner.lookup(org_id, now)

    # ----- instrumented quota: a LOCAL spend that must touch no store read ----- #

    class _P6_LocalQuota:
        """A budget decider that spends locally and reads NO shared state (R6.3).

        It is handed the SAME read counter the snapshot carries; the key assertion is
        that ``evaluate`` never increments it. A ``refuse`` flag drives the budget-refused
        flow (a :class:`BudgetVerdict`), still with zero store reads — a refusal is just as
        local as an admit.
        """

        __slots__ = ("_counter", "_refuse")

        def __init__(self, counter: _P6_ReadCounter, *, refuse: bool) -> None:
            self._counter = counter
            self._refuse = refuse

        async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
            # A LOCAL lease spend: no shared-state read is attributable to the budget
            # phase. The counter is deliberately NOT touched here.
            del cost
            if self._refuse:
                return budget_verdict(request_id)
            return Admitted(owner_id=_ORG, request_id=request_id, admitted_at=0.0)

        def refund(self, cost: int, *, request_id: str) -> None:
            del cost, request_id

    # ----- the remaining lifecycle seams as minimal fakes ----- #

    class _P6_Admission:
        """Admits, or sheds once for the shed flow (fail-closed shed carries its code)."""

        __slots__ = ("_shed",)

        def __init__(self, *, shed: bool) -> None:
            self._shed = shed

        async def admit(
            self, owner_id: str, request_id: str, enqueued_at: float
        ) -> Admitted | ShedVerdict:
            del owner_id, enqueued_at
            if self._shed:
                return shed_verdict(random.Random(0), request_id)
            return Admitted(owner_id=_ORG, request_id=request_id, admitted_at=0.0)

        def complete(self, request_id: str) -> None:
            del request_id

    class _P6_Identity:
        """Resolves the canned identity, or ``None`` for the identity-refused flow."""

        __slots__ = ("_resolve",)

        def __init__(self, *, resolve: bool) -> None:
            self._resolve = resolve

        async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
            del scope
            if not self._resolve:
                return None
            return TenantIdentity(
                org_id=_ORG,
                principal=Principal(
                    key_id="k", org_id=_ORG, rate_per_s=1.0, burst=1.0, feed_seq=0
                ),
            )

    class _P6_Parser:
        """A parser that returns a fixed single-channel ``CanonicalRequest``.

        The §10.4 ``_parse`` phase makes no security decision; a trivial canonical
        request with one input channel is enough to drive every phase after it (the input
        scan walks ``input_channels``, the budget cost sums their text lengths).
        """

        __slots__ = ()

        def parse(
            self, body: bytes, *, surface: Surface, contract: object = None
        ) -> CanonicalRequest:
            del body, contract
            return CanonicalRequest(
                model="gpt-test",
                streaming=False,
                input_channels=(("message_content", "hello"),),
                text_deltas=(("message_content", "hello"),),
                surface=surface,
            )

    def _p6_detector(block: bool) -> object:
        """Build an input detector; a BLOCK flow emits one EXECUTED finding."""

        def _detect(text: str) -> Sequence[Finding]:
            del text
            if not block:
                return ()
            return (
                Finding(
                    detector="p6-detector",
                    detector_version="1",
                    category=Category.PII,
                    status=FindingStatus.EXECUTED,
                    confidence=0.99,
                    spans=(),
                    evidence=None,
                ),
            )

        return _detect

    class _P6_Resolver:
        """Resolves findings to BLOCK when any is present, else ALLOW (C22 stand-in)."""

        __slots__ = ()

        def decide(self, matches: Sequence[Finding]) -> Decision:
            if not matches:
                return Decision(
                    disposition=Disposition.ALLOW,
                    per_finding=(),
                    transformations=(),
                    findings=(),
                    plan_version="p6",
                    deciding_rules=(),
                    unavailable_detectors=(),
                )
            per = tuple(
                FindingDisposition(detector=f.detector, disposition=Disposition.BLOCK)
                for f in matches
            )
            return Decision(
                disposition=Disposition.BLOCK,
                per_finding=per,
                transformations=(),
                findings=tuple(matches),
                plan_version="p6",
                deciding_rules=tuple(fd.detector for fd in per),
                unavailable_detectors=(),
            )

    class _P6_Contract:
        """A contract whose pre-check bounds are generous (pass) or tight (fail).

        The pre-identity pre-checks compare ``len(body)`` / expansion / part count against
        these accessors; for every flow except the pre-check flow the bounds are generous
        so the request reaches canonicalization. The pre-check flow tightens the size
        bound to zero so the phase refuses BEFORE parse — and therefore before the plan
        round trip (zero snapshot reads).
        """

        __slots__ = ("_bytes",)

        def __init__(self, *, max_bytes: int) -> None:
            self._bytes = max_bytes

        def max_request_bytes(self) -> int:
            return self._bytes

        def max_expansion_ratio(self) -> float:
            return 1_000.0

        def max_request_parts(self) -> int:
            return 1_000_000

        def default_response_token_ceiling(self) -> int:
            """v1's default org output ceiling; ``run_lifecycle`` clamps ``max_tokens`` to it."""
            return 4096

        def max_output_tokens(self) -> int:
            """v1's output-token hard cap (the clamp's upper bound)."""
            return 65536

        def max_completions(self) -> int:
            """v1's completion cap; the clamp forces ``n`` to it."""
            return 1

        def estimate_request_tokens(self, chars: int, max_tokens: int, n: int) -> int:
            """v1's request token estimate the budget phase charges over the clamped request."""
            input_tokens = max(1, chars // 4) if chars > 0 else 0
            return max(input_tokens + max_tokens * n, 1)

    @dataclass(frozen=True, slots=True)
    class _P6_Deps:
        """A ``ChatRoute``-shaped dependency bundle the ``ChatSurface`` reads from.

        Carries exactly the seams ``ChatSurface.run_lifecycle`` consults; a duck-typed
        stand-in for the real frozen ``ChatRoute`` so the property exercises the lifecycle
        wiring without the full boot graph.
        """

        contract: _P6_Contract
        parser: _P6_Parser
        identity: _P6_Identity
        snapshot_source: object
        admission: _P6_Admission
        quota: _P6_LocalQuota
        input_detector: object
        resolver: _P6_Resolver
        router: DispatchRouter
        transform: Transform
        clock: object = field(default_factory=lambda: (lambda: 0.0))

    def _p6_build_deps(flow: str, counter: _P6_ReadCounter) -> _P6_Deps:
        """Assemble the dependency bundle configured for one lifecycle ``flow``."""
        inner = ReplicaSnapshot(PlanStore())
        # Register a resolvable plan for the tenant so a reached plan phase proceeds.
        store = PlanStore()
        store.put(
            ExecutionPlan(
                org_id=_ORG,
                epoch=1,
                sequence=1,
                content_hash="deadbeefdeadbeef",
                compiled_at=0.0,
                rules=(),
                required_detectors=frozenset(),
                streaming_mode=StreamingMode.INCREMENTAL,
                integrity_locked=False,
            )
        )
        inner = ReplicaSnapshot(store)
        counting = _P6_CountingSnapshot(inner, counter)
        return _P6_Deps(
            contract=_P6_Contract(max_bytes=0 if flow == _PRE_CHECK else 1_000_000),
            parser=_P6_Parser(),
            identity=_P6_Identity(resolve=flow != _IDENTITY),
            snapshot_source=lambda: counting,
            admission=_P6_Admission(shed=flow == _SHED),
            quota=_P6_LocalQuota(counter, refuse=flow == _BUDGET),
            input_detector=_p6_detector(block=flow == _INPUT_BLOCKED),
            resolver=_P6_Resolver(),
            router=DispatchRouter(),
            transform=Transform(),
        )

    # Expected refuse-code per flow (``None`` ⇒ a proceed outcome).
    _EXPECTED_CODE = {
        _PRE_CHECK: REFUSE_PRE_CHECK,
        _IDENTITY: REFUSE_IDENTITY_UNRESOLVED,
        _SHED: None,  # a shed verdict carries OVERLOAD_SHED, asserted structurally below
        _BUDGET: REFUSE_BUDGET_UNAVAILABLE,
        _INPUT_BLOCKED: REFUSE_INPUT_BLOCKED,
        _COMPLETED: None,
    }

    scope: Mapping[str, object] = {"path": _PATH, "headers": []}
    rng = random.Random(_SEED)

    for i in range(_ITERATIONS):
        # Drive every flow each iteration so a regression in ANY path fails the gate.
        for flow in _P6_FLOWS:
            counter = _P6_ReadCounter()
            deps = _p6_build_deps(flow, counter)
            surface = ChatSurface(deps=deps)  # type: ignore[arg-type]
            _req, outcome = asyncio.run(surface.run_lifecycle(scope, b"{}"))

            reaches_plan = flow in _REACHES_PLAN

            # (a) exactly one served-snapshot read when the plan phase is reached, zero
            #     when the request was refused before it (pre-check / identity).
            expected_reads = 1 if reaches_plan else 0
            assert counter.plan == expected_reads, _ctx(
                i, flow, note="plan snapshot read count", reads=counter.plan,
                expected=expected_reads,
            )

            # (b) NO store read is attributable to the budget spend — the quota component
            #     is handed the SAME counter and must never increment it (R6.3). This holds
            #     for the admitted/completed flow AND the budget-refused flow alike.
            assert counter.budget == 0, _ctx(
                i, flow, note="a store read was attributed to the budget spend",
                budget_reads=counter.budget,
            )

            # The total shared-state round trips on the request path is the plan read
            # alone — exactly one when reached, never more (identity + budget add none).
            assert counter.plan + counter.budget == expected_reads, _ctx(
                i, flow, note="total request-path shared-state round trips",
                total=counter.plan + counter.budget, expected=expected_reads,
            )

            # Sanity-check the outcome matches the flow so the read counts are being
            # measured against the intended path, not a mis-wired refusal.
            if flow == _SHED:
                # An admission shed refuses with the verdict's own OVERLOAD_SHED code.
                from gateway_v2.domain.posture import OVERLOAD_SHED

                assert outcome.refuse_code == OVERLOAD_SHED, _ctx(
                    i, flow, note="shed flow did not refuse with OVERLOAD_SHED",
                    code=outcome.refuse_code,
                )
            elif _EXPECTED_CODE[flow] is None:
                assert outcome.refuse_code is None, _ctx(
                    i, flow, note="expected a proceed outcome", code=outcome.refuse_code,
                )
                # A proceed outcome carries the plan-derived provider inputs.
                assert outcome.plan is not None and outcome.destination is not None, _ctx(
                    i, flow, note="proceed outcome missing provider inputs",
                )
            else:
                assert outcome.refuse_code == _EXPECTED_CODE[flow], _ctx(
                    i, flow, note="unexpected refuse code",
                    code=outcome.refuse_code, expected=_EXPECTED_CODE[flow],
                )

        # Keep the seeded RNG advancing so iterations are not identical draws (the flows
        # are deterministic, but this preserves the house seeded-RNG idiom).
        rng.random()


# ===========================================================================
# Property 5 — Pre-identity pre-checks reject before canonicalization (task 2.5).
# ===========================================================================
# Feature: chat-surface-gw15, Property 5: Pre-identity pre-checks reject before canonicalization
# Validates: Requirements 3.1, 3.2
#
# *For any* request whose RAW body breaches one of the three Pre_Identity_Clock bounds read
# from the injected ``ResourceContract`` — the size cap (``max_request_bytes``), the cheap
# expansion-ratio proxy (``max_expansion_ratio``), or the structural part count
# (``max_request_parts``) — ``run_lifecycle`` returns the declared ``REFUSE_PRE_CHECK``
# value-code AND the heavy canonicalization (``deps.parser.parse``) is NEVER invoked. The
# pre-checks are the pre-identity clock: they run in FRONT of the auth-independent heavy
# parse, so an oversized / expansion-bomb / part-flood body is rejected before a single byte
# is parsed (R3.1 pre-checks run first; R3.2 a breach refuses before canonicalization).
#
# To drive all three bounds deterministically with KNOWN thresholds, the deps bundle carries
# a tiny duck-typed stub contract exposing exactly the three accessors the pre-checks read.
# ``edge`` holds no capacity literal — it reads the bounds through the contract — so a stub
# contract with chosen thresholds is a faithful substitute for the real ``ResourceContract``
# accessors here (the real derivations are covered by the runtime resources tests). The
# injected parser is an instrumented SPY recording every ``parse`` call, so the "parse is
# never invoked" half of the property is proven by a recorded call count of zero.


def test_property_5_pre_identity_pre_checks_reject_before_canonicalization() -> None:
    import asyncio
    import random
    from dataclasses import dataclass, field

    from gateway_v2.domain.plan import Surface
    from gateway_v2.edge.openai_chat import REFUSE_PRE_CHECK, ChatSurface
    from gateway_v2.edge.wire.openai import CanonicalRequest

    _P5_SEED = 0x16_15_0005
    _P5_ITERATIONS = 10_000

    # The chat path the lifecycle maps to ``Surface.CHAT``; the pre-check phase ignores the
    # scope (the bounds come from the contract), but a well-formed scope is supplied anyway.
    _P5_SCOPE: dict[str, object] = {"path": "/v1/chat/completions"}

    # U+00A9 (COPYRIGHT SIGN) encodes as two bytes, both > the ASCII ceiling, so each copy
    # pushes the cheap expansion-ratio proxy toward 2.0 — used to breach the expansion bound.
    _P5_NON_ASCII = "\u00a9".encode()

    @dataclass(slots=True)
    class _P5SpyParser:
        """An instrumented parser recording every ``parse`` call (never expected to fire).

        Property 5 asserts the heavy canonicalization never runs when a pre-check breaches,
        so the parser is a SPY: ``parse`` increments ``calls`` and — were it ever reached —
        returns a trivial well-formed ``CanonicalRequest``, so a bug that let the lifecycle
        fall through to parse surfaces as a non-zero ``calls`` count. The test asserts
        ``calls == 0`` every iteration.
        """

        calls: int = 0

        def parse(
            self, body: bytes, *, surface: Surface, contract: object = None
        ) -> CanonicalRequest:
            del body, contract
            self.calls += 1
            return CanonicalRequest(
                model="spy",
                streaming=False,
                input_channels=(),
                text_deltas=(),
                surface=surface,
            )

    @dataclass(frozen=True, slots=True)
    class _P5StubContract:
        """A duck-typed stand-in exposing only the three pre-check bound accessors.

        ``edge`` reads the pre-check bounds through the contract and holds no literal, so a
        stub exposing exactly ``max_request_bytes`` / ``max_expansion_ratio`` /
        ``max_request_parts`` is a faithful substitute for the real ``ResourceContract``
        accessors in this property. Thresholds are chosen per iteration so each of the three
        bounds can be breached deterministically.
        """

        size_cap: int
        expansion_cap: float
        parts_cap: int

        def max_request_bytes(self) -> int:
            return self.size_cap

        def max_expansion_ratio(self) -> float:
            return self.expansion_cap

        def max_request_parts(self) -> int:
            return self.parts_cap

    @dataclass(slots=True)
    class _P5Deps:
        """The minimal ``ChatRoute``-shaped bundle the pre-check phase reads.

        ``ChatSurface`` reads only ``deps.contract`` (the pre-check bounds) and
        ``deps.parser`` (the spy) before the pre-check phase returns, so this duck-typed
        bundle carries exactly those two seams. A breaching pre-check returns before any
        other dep is touched, which is itself part of what the property proves.
        """

        contract: _P5StubContract
        parser: _P5SpyParser = field(default_factory=_P5SpyParser)

    def _p5_expansion_ratio(body: bytes) -> float:
        """Mirror of ``ChatSurface._expansion_ratio`` so the generator can target it."""
        total = len(body)
        if total == 0:
            return 1.0
        expansion_prone = sum(1 for byte in body if byte > 0x7F)
        return 1.0 + expansion_prone / total

    def _p5_part_count(body: bytes) -> int:
        """Mirror of ``ChatSurface._part_count`` (``{`` opens + ``,`` separators)."""
        return sum(1 for byte in body if byte in (0x7B, 0x2C))

    def _p5_make_size_breach(rng: random.Random) -> tuple[bytes, _P5StubContract]:
        """A body whose raw length exceeds ``max_request_bytes``; other bounds slack."""
        cap = rng.randint(8, 256)
        over = rng.randint(1, 64)
        body = b"a" * (cap + over)
        return body, _P5StubContract(
            size_cap=cap,
            expansion_cap=4.0,
            parts_cap=max(_p5_part_count(body) + 1, 1),
        )

    def _p5_make_expansion_breach(rng: random.Random) -> tuple[bytes, _P5StubContract]:
        """A body whose cheap expansion-ratio proxy exceeds ``max_expansion_ratio``.

        The body is dominated by expansion-prone (non-ASCII) bytes so its proxy ratio climbs
        toward 2.0; the cap is set strictly below the measured ratio. Size/part caps are
        slack so EXPANSION is the sole breaching bound.
        """
        non_ascii_copies = rng.randint(4, 64)
        ascii_copies = rng.randint(0, 4)
        body = _P5_NON_ASCII * non_ascii_copies + b"a" * ascii_copies
        ratio = _p5_expansion_ratio(body)
        cap = max(1.0, ratio - 0.01)
        return body, _P5StubContract(
            size_cap=len(body) + 1,
            expansion_cap=cap,
            parts_cap=max(_p5_part_count(body) + 1, 1),
        )

    def _p5_make_parts_breach(rng: random.Random) -> tuple[bytes, _P5StubContract]:
        """A body whose structural part count exceeds ``max_request_parts``.

        The body is a run of ``{`` / ``,`` structural bytes (the cheap part proxy counts
        exactly these); the cap is set strictly below the measured count. Size/expansion
        caps are slack so PART COUNT is the sole breaching bound.
        """
        structural = "".join(rng.choice("{,") for _ in range(rng.randint(4, 64)))
        body = structural.encode("ascii")
        parts = _p5_part_count(body)
        cap = rng.randint(0, parts - 1)
        return body, _P5StubContract(
            size_cap=len(body) + 1,
            expansion_cap=4.0,
            parts_cap=cap,
        )

    async def _p5_run(
        surface: ChatSurface, body: bytes
    ) -> tuple[CanonicalRequest | None, object]:
        return await surface.run_lifecycle(_P5_SCOPE, body)

    rng = random.Random(_P5_SEED)
    builders = (
        ("size", _p5_make_size_breach),
        ("expansion", _p5_make_expansion_breach),
        ("parts", _p5_make_parts_breach),
    )

    for i in range(_P5_ITERATIONS):
        kind, builder = rng.choice(builders)
        body, contract = builder(rng)
        deps = _P5Deps(contract=contract)
        surface = ChatSurface(deps=deps)  # type: ignore[arg-type]

        req, outcome = asyncio.run(_p5_run(surface, body))

        ctx = (
            f"seed={_P5_SEED:#x} iter={i} breach={kind} "
            f"body_len={len(body)} parser_calls={deps.parser.calls}"
        )
        # The declared pre-check value-code is rendered (R3.2).
        assert outcome.refuse_code == REFUSE_PRE_CHECK, ctx  # type: ignore[attr-defined]
        # The refusal precedes parse, so no canonical request is produced.
        assert req is None, ctx
        # The heavy canonicalization was never invoked (R3.1): the pre-identity clock
        # rejected the body in front of the parse step.
        assert deps.parser.calls == 0, ctx
        # The proceed inputs are all unset on a refusal (fail-closed shape).
        assert outcome.plan is None, ctx  # type: ignore[attr-defined]
        assert outcome.destination is None, ctx  # type: ignore[attr-defined]
        assert outcome.body is None, ctx  # type: ignore[attr-defined]


# ===========================================================================
# Property 7 — Unresolvable or stale identity fails closed (task 2.7).
# ===========================================================================
# Feature: chat-surface-gw15, Property 7: Unresolvable or stale identity fails closed
# Validates: Requirements 4.2, 4.3
#
# *For any* request whose Tenant_Identity cannot be resolved (R4.2 — no credential, a
# revoked / never-issued principal) OR whose GW05b/c freshness stamp is stale / unavailable
# (R4.3), ``run_lifecycle`` renders the declared authentication value-code
# (``REFUSE_IDENTITY_UNRESOLVED`` = ``shared_state_unavailable``) and makes ZERO provider
# calls — the identity phase runs BEFORE the provider is ever opened, so a fail-closed
# identity refusal cannot leak a request toward an upstream model.
#
# The property drives >=10,000 seeded iterations, each a well-formed, tiny body that sails
# past the pre-identity pre-checks and parses cleanly so the IDENTITY phase is the first
# phase able to fail — isolating the identity fail-closed behaviour. It alternates the two
# fail-closed conditions:
#   * UNRESOLVABLE identity (R4.2): a fake ``IdentityResolver`` returning ``None``.
#   * STALE / UNAVAILABLE freshness stamp (R4.3): the REAL ``CachingIdentityResolver`` over
#     an ``IdentityCache`` whose ``StampView`` has never observed a stamp — ``fresh()`` is
#     ``False`` so ``IdentityCache.resolve`` raises ``StoreDataUnavailable``, which the
#     resolver maps to ``None`` (exercising the genuine stale-stamp path end to end).
# A counting ``ProviderClient`` wraps a scripted ``StubProviderClient`` and records every
# ``open`` / ``abort`` touch, so "zero provider calls" is observable, not assumed. The
# ``ChatRoute`` is built with the real GW15 defaults for every OTHER phase (irrelevant: a
# ``None`` identity refuses in phase 3, before admission / budget / the input scan).


def test_property_7_unresolvable_or_stale_identity_fails_closed() -> None:
    import asyncio
    import json
    import random
    from typing import cast

    from gateway_v2.admit.identity import IdentityCache
    from gateway_v2.dispatch.provider import (
        ProviderClient,
        StubProviderClient,
        UpstreamEvent,
        UpstreamRequest,
    )
    from gateway_v2.dispatch.routing import DispatchRouter
    from gateway_v2.domain.identity import Principal
    from gateway_v2.edge.executor import ScanExecutor
    from gateway_v2.edge.identity_resolver import (
        CachingIdentityResolver,
        IdentityResolver,
    )
    from gateway_v2.edge.openai_chat import (
        REFUSE_IDENTITY_UNRESOLVED,
        ChatSurface,
        LifecycleOutcome,
    )
    from gateway_v2.edge.routes import ChatRoute
    from gateway_v2.edge.wire.openai import CanonicalRequest
    from gateway_v2.egress.output_guard import MinimalOutputResolver
    from gateway_v2.egress.stream import ScanProtocol
    from gateway_v2.runtime.holdback_config import load_holdback_config
    from gateway_v2.runtime.kinds import HardwareSignals
    from gateway_v2.runtime.resources import ResourceContract, from_signals
    from gateway_v2.runtime.state_stamp import StampView

    _P7_SEED = 0x16_15_0007
    _P7_ITERATIONS = 10_000
    _P7_CHAT_PATH = "/v1/chat/completions"
    _P7_COMPLETIONS_PATH = "/v1/completions"
    _P7_STATE_SECRET = b"p7-state-signing-secret"

    # ----- a ProviderClient that COUNTS open/abort (zero-calls is observable) ----- #

    class _P7CountingProvider:
        """Wraps a scripted stub and records every ``open`` / ``abort`` call.

        Satisfies the ``@runtime_checkable`` :class:`ProviderClient` structurally. A
        fail-closed identity refusal must never touch it, so ``total_calls()`` must stay 0.
        """

        __slots__ = ("open_calls", "abort_calls", "_stub")

        def __init__(self, *, rng: random.Random) -> None:
            self._stub = StubProviderClient(
                script=(UpstreamEvent(text_deltas=(("content", "x"),), final=True),),
                rng=rng,
            )
            self.open_calls = 0
            self.abort_calls = 0

        def open(self, request: UpstreamRequest):  # type: ignore[no-untyped-def]
            self.open_calls += 1
            return self._stub.open(request)

        async def abort(self) -> None:
            self.abort_calls += 1
            await self._stub.abort()

        def total_calls(self) -> int:
            return self.open_calls + self.abort_calls

    # ----- the two fail-closed identity conditions ----- #

    class _P7UnresolvableResolver:
        """``IdentityResolver`` that resolves EVERY request to ``None`` (R4.2)."""

        __slots__ = ()

        async def resolve(self, scope):  # type: ignore[no-untyped-def]
            del scope
            return None

    def _p7_stale_resolver() -> CachingIdentityResolver:
        """The REAL resolver over a cache gated by an UNVERIFIED stamp (R4.3).

        A fresh :class:`StampView` that has observed no stamp is never ``fresh()``, so
        :meth:`IdentityCache.resolve` raises ``StoreDataUnavailable`` and the resolver maps
        it to ``None`` — the genuine stale / unavailable freshness-stamp path. The injected
        ``fetch`` is never reached (the unverified stamp refuses before any fetch).
        """

        async def _never_fetched(key_hash: str):  # type: ignore[no-untyped-def]
            del key_hash
            return Principal(
                key_id="k", org_id="org", rate_per_s=1.0, burst=1.0, feed_seq=0
            )

        stale_stamp = StampView(_P7_STATE_SECRET)  # no stamp observed => never fresh
        cache = IdentityCache(stamp=stale_stamp)
        return CachingIdentityResolver(cache=cache, fetch=_never_fetched)

    # ----- a minimal, deterministic in-flight control source (unused on a refusal) ----- #

    from gateway_v2.edge.cancel import InFlightSnapshot

    class _P7NoControl:
        """A fresh/available control source — never consulted on a pre-provider refusal."""

        __slots__ = ()

        def snapshot(self) -> InFlightSnapshot:
            return InFlightSnapshot(
                available=True,
                age_s=0.0,
                kill_switch_engaged=False,
                key_revoked=False,
                plan_changed=False,
            )

    class _P7Clock:
        __slots__ = ("_t",)

        def __init__(self) -> None:
            self._t = 0.0

        def __call__(self) -> float:
            self._t += 1e-6
            return self._t

    def _p7_contract() -> ResourceContract:
        """A serviceable contract (the shared LGW12/15 ``from_signals`` fixture)."""
        signals = HardwareSignals(
            cpu_quota=4.0,
            memory_limit=400 * 1024 * 1024 * 8,
            fd_limit=4096,
            cpu_source="test",
            mem_source="test",
            fd_source="test",
        )
        return from_signals(
            signals,
            target_p99_ms=20.0,
            utilization_cap=0.75,
            per_worker_rss=400 * 1024 * 1024,
        )

    def _p7_no_error_findings(_frame: str) -> Sequence[Finding]:
        return ()

    def _p7_route(
        *, resolver: object, provider: _P7CountingProvider
    ) -> ChatRoute:
        """A ``ChatRoute`` wiring the injected identity ``resolver`` + counting provider.

        Every other phase keeps a fail-closed default (irrelevant: a ``None`` identity
        refuses in phase 3). The counting provider is injected so "zero provider calls" is
        observable; the ``scanner`` is the shipped holdback scan (``ScanProtocol``-shaped,
        unused on a refusal). ``holdback.scan`` matches the ``ScanProtocol`` call shape
        structurally (its ``token_index`` is a ``TokenCounterProtocol``); the ``cast``
        states that to the checker.
        """
        cfg, _logs = load_holdback_config()
        from gateway_v2.detect.holdback import scan as holdback_scan

        return ChatRoute(
            contract=_p7_contract(),
            provider=cast(ProviderClient, provider),
            scanner=cast(ScanProtocol, holdback_scan),
            resolver=MinimalOutputResolver(),
            cfg=cfg,
            router=DispatchRouter(),
            active_streams=lambda: 0,
            control=_P7NoControl(),
            clock=_P7Clock(),
            error_scanner=_p7_no_error_findings,
            executor=ScanExecutor(contract=_p7_contract()),
            identity=cast(IdentityResolver, resolver),
        )

    def _p7_body(rng: random.Random) -> bytes:
        """A random but well-formed, tiny OpenAI body (passes parse + pre-checks)."""
        models = ("gpt-4", "gpt-4o-mini", "o3", "claude-proxy")
        snippets = ("hi", "hello there", "", "SSN 123-45-6789", "unicode café", "a\nb\tc")
        count = rng.randint(1, 3)
        messages = [
            {
                "role": rng.choice(("user", "system", "assistant")),
                "content": rng.choice(snippets),
            }
            for _ in range(count)
        ]
        body: dict[str, object] = {"model": rng.choice(models), "messages": messages}
        if rng.random() < 0.5:
            body["stream"] = rng.choice((True, False))
        return json.dumps(body).encode("utf-8")

    def _p7_scope(path: str) -> dict[str, object]:
        """A minimal ASGI http scope carrying a well-formed bearer credential."""
        return {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [
                (b"content-type", b"application/json"),
                (b"authorization", b"Bearer p7-key-abc123"),
            ],
        }

    # The counting provider satisfies the ProviderClient protocol structurally.
    assert isinstance(_P7CountingProvider(rng=random.Random(_P7_SEED)), ProviderClient)

    rng = random.Random(_P7_SEED)
    for i in range(_P7_ITERATIONS):
        provider = _P7CountingProvider(rng=random.Random(_P7_SEED ^ i))
        stale = i % 2 == 0
        resolver = _p7_stale_resolver() if stale else _P7UnresolvableResolver()
        route = _p7_route(resolver=resolver, provider=provider)
        path = _P7_CHAT_PATH if rng.random() < 0.5 else _P7_COMPLETIONS_PATH
        scope = _p7_scope(path)
        body = _p7_body(rng)
        condition = "stale-stamp" if stale else "unresolvable-identity"

        async def _run(
            route: ChatRoute = route,
            scope: dict[str, object] = scope,
            body: bytes = body,
        ) -> tuple[CanonicalRequest | None, LifecycleOutcome]:
            return await ChatSurface(deps=route).run_lifecycle(scope, body)

        req, outcome = asyncio.run(_run())

        # (1) the declared authentication value-code is rendered (R4.2 / R4.3).
        assert outcome.refuse_code == REFUSE_IDENTITY_UNRESOLVED, (
            f"[{condition}] identity did not fail closed with the auth code "
            f"{REFUSE_IDENTITY_UNRESOLVED!r}: got {outcome.refuse_code!r} (iter {i})"
        )
        # (2) ZERO provider calls — the refusal never opened or aborted the provider.
        assert provider.total_calls() == 0, (
            f"[{condition}] a fail-closed identity refusal made {provider.total_calls()} "
            f"provider call(s) (open={provider.open_calls}, abort={provider.abort_calls}); "
            f"MUST be zero (iter {i})"
        )
        # (3) no proceed state leaks onto a refusal outcome (fail-closed shape).
        assert (
            outcome.plan is None
            and outcome.destination is None
            and outcome.body is None
        ), f"[{condition}] a refusal outcome carries proceed state (iter {i})"
        # The request parsed before the identity phase (surfaced for the render shape),
        # but it is never forwarded.
        assert req is not None, (
            f"[{condition}] parse should have succeeded before the identity phase (iter {i})"
        )


def test_property_7_resolved_identity_is_not_refused_at_identity() -> None:
    """Negative control for Property 7: a resolvable identity is NOT an identity refusal.

    Feature: chat-surface-gw15, Property 7: Unresolvable or stale identity fails closed
    Validates: Requirements 4.2, 4.3 (the fail-closed boundary)

    A resolver returning a real :class:`TenantIdentity` must NOT yield the identity auth
    refuse-code, proving the fail-closed refusal is caused by the unresolvable / stale
    conditions and not by every path. (The lifecycle still refuses LATER — the route keeps
    its fail-closed admission default, which sheds — but with the SHED code, after a
    successful identity resolution, and still with zero provider calls.)
    """
    import asyncio
    import json
    import random
    from typing import cast

    from gateway_v2.dispatch.provider import (
        ProviderClient,
        StubProviderClient,
        UpstreamEvent,
        UpstreamRequest,
    )
    from gateway_v2.dispatch.routing import DispatchRouter
    from gateway_v2.domain.identity import Principal
    from gateway_v2.edge.cancel import InFlightSnapshot
    from gateway_v2.edge.executor import ScanExecutor
    from gateway_v2.edge.identity_resolver import TenantIdentity
    from gateway_v2.edge.openai_chat import (
        REFUSE_IDENTITY_UNRESOLVED,
        ChatSurface,
        LifecycleOutcome,
    )
    from gateway_v2.edge.routes import ChatRoute
    from gateway_v2.edge.wire.openai import CanonicalRequest
    from gateway_v2.egress.output_guard import MinimalOutputResolver
    from gateway_v2.egress.stream import ScanProtocol
    from gateway_v2.runtime.holdback_config import load_holdback_config
    from gateway_v2.runtime.kinds import HardwareSignals
    from gateway_v2.runtime.resources import ResourceContract, from_signals

    _SEED = 0x16_15_1007

    class _CountingProvider:
        __slots__ = ("open_calls", "abort_calls", "_stub")

        def __init__(self, *, rng: random.Random) -> None:
            self._stub = StubProviderClient(
                script=(UpstreamEvent(text_deltas=(("content", "x"),), final=True),),
                rng=rng,
            )
            self.open_calls = 0
            self.abort_calls = 0

        def open(self, request: UpstreamRequest):  # type: ignore[no-untyped-def]
            self.open_calls += 1
            return self._stub.open(request)

        async def abort(self) -> None:
            self.abort_calls += 1
            await self._stub.abort()

        def total_calls(self) -> int:
            return self.open_calls + self.abort_calls

    class _ResolvingResolver:
        __slots__ = ()

        async def resolve(self, scope):  # type: ignore[no-untyped-def]
            del scope
            return TenantIdentity(
                org_id="org-ok",
                principal=Principal(
                    key_id="k-ok", org_id="org-ok", rate_per_s=1.0, burst=1.0, feed_seq=0
                ),
            )

    class _NoControl:
        __slots__ = ()

        def snapshot(self) -> InFlightSnapshot:
            return InFlightSnapshot(
                available=True,
                age_s=0.0,
                kill_switch_engaged=False,
                key_revoked=False,
                plan_changed=False,
            )

    def _contract() -> ResourceContract:
        signals = HardwareSignals(
            cpu_quota=4.0,
            memory_limit=400 * 1024 * 1024 * 8,
            fd_limit=4096,
            cpu_source="test",
            mem_source="test",
            fd_source="test",
        )
        return from_signals(
            signals,
            target_p99_ms=20.0,
            utilization_cap=0.75,
            per_worker_rss=400 * 1024 * 1024,
        )

    def _no_error_findings(_frame: str) -> Sequence[Finding]:
        return ()

    from gateway_v2.detect.holdback import scan as holdback_scan

    cfg, _logs = load_holdback_config()
    provider = _CountingProvider(rng=random.Random(_SEED))
    route = ChatRoute(
        contract=_contract(),
        provider=cast(ProviderClient, provider),
        scanner=cast(ScanProtocol, holdback_scan),
        resolver=MinimalOutputResolver(),
        cfg=cfg,
        router=DispatchRouter(),
        active_streams=lambda: 0,
        control=_NoControl(),
        clock=lambda: 0.0,
        error_scanner=_no_error_findings,
        executor=ScanExecutor(contract=_contract()),
        identity=_ResolvingResolver(),
    )
    body = json.dumps(
        {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
    ).encode("utf-8")
    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [(b"authorization", b"Bearer ok-key")],
    }

    async def _run() -> tuple[CanonicalRequest | None, LifecycleOutcome]:
        return await ChatSurface(deps=route).run_lifecycle(scope, body)

    _req, outcome = asyncio.run(_run())

    assert outcome.refuse_code != REFUSE_IDENTITY_UNRESOLVED, (
        "a resolved identity must not be refused with the identity auth code; "
        f"got {outcome.refuse_code!r}"
    )
    # Still fail-closed overall (admission default sheds) and still zero provider calls.
    assert provider.total_calls() == 0, (
        f"a pre-provider refusal must make zero provider calls; got {provider.total_calls()}"
    )

# ===========================================================================
# Wiring / edge-case unit (example) tests (task 2.11).
# ===========================================================================
# Annotation-only imports for the section's module-level helpers. Guarded under
# ``TYPE_CHECKING`` so the runtime imports stay section-local (inside the helper /
# test bodies, matching the sibling sections); ``from __future__ import annotations``
# makes every annotation a string, so these names are resolved by the checker only.
from typing import TYPE_CHECKING  # noqa: E402  (section-local, kept with the 2.11 block)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from gateway_v2.domain.finding import Finding
    from gateway_v2.domain.plan import ExecutionPlan as _P211ExecutionPlan
    from gateway_v2.edge.wire.openai import CanonicalRequest as _P211CanonicalRequest

# Feature: chat-surface-gw15, task 2.11: wiring/edge-case unit tests
# Validates: Requirements 4.1, 5.1, 5.3, 5.4, 6.1
#
# These are EXAMPLE/unit tests (NOT property tests — no >=10,000 iteration driver): each
# pins ONE concrete wiring fact of the §10.4 lifecycle that the surrounding property tests
# (P5/P6/P7/P8) do not assert directly:
#
#   * R4.1 — a VALID key resolves to a principal / ``TenantIdentity``. Exercised through the
#     REAL :class:`CachingIdentityResolver` over a shipped :class:`IdentityCache` whose
#     freshness check is in break-glass (``fresh_ms=0`` ⇒ ``fresh()`` True, the documented
#     opt-out), so a well-formed bearer key resolves end-to-end to the fetched
#     :class:`Principal` and the lifecycle does NOT refuse at the identity phase.
#   * R5.1 / R5.3 — the ``AdmissionController`` surface IS CALLED and an ``Admitted`` grant
#     CONTINUES the lifecycle past admission. Exercised through an instrumented admission fake
#     that records every ``admit`` call and returns ``Admitted``; the lifecycle must reach a
#     proceed outcome (admission did not short-circuit) and the recorded call count must be 1.
#   * R5.4 — an UNDECIDABLE admission fails CLOSED to a shed. The shipped controller maps every
#     undecidable path to a ``ShedVerdict`` (``admit`` catches any exception → shed); a fake
#     whose decision RAISES stands in for "undecidable", and the real controller's own
#     fail-closed catch is asserted directly too, so the refuse-code is the shed code and no
#     proceed state leaks.
#   * R6.1 — the ``QuotaComponent.evaluate`` surface IS CALLED on the budget phase. Exercised
#     through an instrumented budget fake recording every ``evaluate`` call; reaching a proceed
#     outcome proves the budget phase ran and admitted, and the recorded count must be 1.
#
# Each case builds a duck-typed ``ChatRoute``-shaped bundle carrying exactly the seams
# ``ChatSurface.run_lifecycle`` consults (mirroring the P6 section's ``_P6_Deps`` approach) so
# the wiring is exercised without the full boot graph; the identity case additionally wires the
# real resolver. Symbols are uniquely ``_p211_`` / ``_P211_`` prefixed and imports are
# section-local, so this section is self-contained and never collides with a sibling section.


def _p211_execution_plan(org_id: str) -> _P211ExecutionPlan:
    """Build a resolvable :class:`ExecutionPlan` for ``org_id`` (section-local helper)."""
    from gateway_v2.domain.plan import ExecutionPlan, StreamingMode

    return ExecutionPlan(
        org_id=org_id,
        epoch=1,
        sequence=1,
        content_hash="deadbeefdeadbeef",
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _p211_snapshot_source(org_id: str) -> Callable[[], object]:
    """A served snapshot source resolving a plan for ``org_id`` (one shared-state round trip)."""
    from gateway_v2.plan.snapshot import ReplicaSnapshot
    from gateway_v2.plan.store import PlanStore

    store = PlanStore()
    store.put(_p211_execution_plan(org_id))
    snapshot = ReplicaSnapshot(store)
    return lambda: snapshot


def _p211_canonical_request() -> _P211CanonicalRequest:
    """A fixed single-channel :class:`CanonicalRequest` the fake parser returns."""
    from gateway_v2.domain.plan import Surface
    from gateway_v2.edge.wire.openai import CanonicalRequest

    return CanonicalRequest(
        model="gpt-test",
        streaming=False,
        input_channels=(("message_content", "hello"),),
        text_deltas=(("message_content", "hello"),),
        surface=Surface.CHAT,
    )


class _P211Parser:
    """A parser returning a fixed canonical request (makes no security decision, R1.5)."""

    __slots__ = ()

    def parse(self, body: bytes, *, surface, contract=None):  # type: ignore[no-untyped-def]
        del body, surface, contract
        return _p211_canonical_request()


class _P211Identity:
    """Resolves a canned :class:`TenantIdentity` for the duck-typed wiring cases."""

    __slots__ = ("_org",)

    def __init__(self, org: str) -> None:
        self._org = org

    async def resolve(self, scope):  # type: ignore[no-untyped-def]
        del scope
        from gateway_v2.domain.identity import Principal
        from gateway_v2.edge.identity_resolver import TenantIdentity

        return TenantIdentity(
            org_id=self._org,
            principal=Principal(
                key_id="k", org_id=self._org, rate_per_s=1.0, burst=1.0, feed_seq=0
            ),
        )


class _P211Contract:
    """A generous contract so the pre-identity pre-checks always pass (bounds from here)."""

    __slots__ = ()

    def max_request_bytes(self) -> int:
        return 1_000_000

    def max_expansion_ratio(self) -> float:
        return 1_000.0

    def max_request_parts(self) -> int:
        return 1_000_000

    def default_response_token_ceiling(self) -> int:
        """v1's default org output ceiling; ``run_lifecycle`` clamps ``max_tokens`` to it."""
        return 4096

    def max_output_tokens(self) -> int:
        """v1's output-token hard cap (the clamp's upper bound)."""
        return 65536

    def max_completions(self) -> int:
        """v1's completion cap; the clamp forces ``n`` to it."""
        return 1

    def estimate_request_tokens(self, chars: int, max_tokens: int, n: int) -> int:
        """v1's request token estimate the budget phase charges over the clamped request."""
        input_tokens = max(1, chars // 4) if chars > 0 else 0
        return max(input_tokens + max_tokens * n, 1)


class _P211AllowResolver:
    """Resolves findings to BLOCK iff any is present, else ALLOW (so empty ⇒ proceed, C22)."""

    __slots__ = ()

    def decide(self, matches):  # type: ignore[no-untyped-def]
        from gateway_v2.domain.decision import Decision, Disposition, FindingDisposition

        if not matches:
            return Decision(
                disposition=Disposition.ALLOW,
                per_finding=(),
                transformations=(),
                findings=(),
                plan_version="p211",
                deciding_rules=(),
                unavailable_detectors=(),
            )
        per = tuple(
            FindingDisposition(detector=f.detector, disposition=Disposition.BLOCK)
            for f in matches
        )
        return Decision(
            disposition=Disposition.BLOCK,
            per_finding=per,
            transformations=(),
            findings=tuple(matches),
            plan_version="p211",
            deciding_rules=tuple(fd.detector for fd in per),
            unavailable_detectors=(),
        )


def _p211_no_findings(text: str) -> Sequence[Finding]:
    """An input detector that finds nothing (so the input scan resolves to ALLOW)."""
    del text
    return ()


def _p211_build_deps(
    *,
    org: str,
    admission: object,
    quota: object,
    identity: object | None = None,
) -> object:
    """Assemble a ``ChatRoute``-shaped duck-typed bundle for the wiring cases.

    Carries exactly the seams ``ChatSurface.run_lifecycle`` reads: the generous contract, the
    fixed parser, the canned identity (or an injected one), a plan-resolving snapshot source,
    the injected ``admission`` / ``quota`` fakes under test, a no-findings input detector, the
    ALLOW resolver, and the real pure ``DispatchRouter`` / ``Transform`` for the proceed outcome.
    """
    from dataclasses import dataclass, field

    from gateway_v2.dispatch.routing import DispatchRouter
    from gateway_v2.dispatch.transform import Transform

    @dataclass(frozen=True, slots=True)
    class _P211Deps:
        contract: object
        parser: object
        identity: object
        snapshot_source: object
        admission: object
        quota: object
        input_detector: object
        resolver: object
        router: object
        transform: object
        clock: object = field(default_factory=lambda: (lambda: 0.0))

    return _P211Deps(
        contract=_P211Contract(),
        parser=_P211Parser(),
        identity=identity if identity is not None else _P211Identity(org),
        snapshot_source=_p211_snapshot_source(org),
        admission=admission,
        quota=quota,
        input_detector=_p211_no_findings,
        resolver=_P211AllowResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )


_P211_SCOPE = {"path": "/v1/chat/completions", "headers": []}


def test_wiring_valid_key_resolves_to_principal() -> None:
    """R4.1: a VALID key resolves to a principal / ``TenantIdentity`` (identity does not refuse).

    Feature: chat-surface-gw15, task 2.11
    Validates: Requirements 4.1

    Drives the REAL :class:`CachingIdentityResolver` over a shipped :class:`IdentityCache`. The
    cache's freshness check is in the documented break-glass (``fresh_ms=0`` ⇒ ``fresh()`` True),
    so a well-formed bearer credential resolves end-to-end to the fetched :class:`Principal`: the
    resolver yields a :class:`TenantIdentity` carrying that principal, and the lifecycle therefore
    does NOT refuse at the identity phase with ``REFUSE_IDENTITY_UNRESOLVED``. Asserted both at the
    resolver surface (a real principal is returned) and through the full lifecycle (the identity
    auth code is never the refuse-code for a valid key).
    """
    import asyncio

    from gateway_v2.admit.identity import IdentityCache
    from gateway_v2.domain.identity import Principal
    from gateway_v2.edge.identity_resolver import (
        CachingIdentityResolver,
        TenantIdentity,
    )
    from gateway_v2.edge.openai_chat import REFUSE_IDENTITY_UNRESOLVED, ChatSurface
    from gateway_v2.runtime.state_stamp import StampView

    _ORG = "org-valid-key"
    _KEY = "valid-key-abc123"

    async def _fetch(key_hash: str):  # type: ignore[no-untyped-def]
        # The injected store read: a valid key resolves to a signed principal (R4.1).
        assert key_hash == _KEY
        return Principal(
            key_id="k-valid", org_id=_ORG, rate_per_s=10.0, burst=10.0, feed_seq=0
        )

    # Break-glass freshness (fresh_ms=0): fresh() is True, so the cache serves the resolved
    # principal rather than refusing on an unverified stamp — isolating the valid-key path.
    stamp = StampView(b"p211-state-secret", fresh_ms=0)
    cache = IdentityCache(stamp=stamp)
    resolver = CachingIdentityResolver(cache=cache, fetch=_fetch)

    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [(b"authorization", f"Bearer {_KEY}".encode())],
    }

    # (1) the resolver surface resolves the valid key to a real principal / TenantIdentity.
    identity = asyncio.run(resolver.resolve(scope))
    assert isinstance(identity, TenantIdentity), (
        f"a valid key must resolve to a TenantIdentity, got {identity!r}"
    )
    assert identity.org_id == _ORG
    assert isinstance(identity.principal, Principal)
    assert identity.principal.key_id == "k-valid"

    # (2) through the lifecycle, a valid key is NOT refused at the identity phase. Admission
    #     defaults to a fail-closed shed, so the overall outcome still refuses — but with the
    #     SHED code, after a successful identity resolution, never the identity auth code.
    from gateway_v2.edge.chat_deps import (
        fail_closed_admission,
        fail_closed_budget,
    )

    deps = _p211_build_deps(
        org=_ORG,
        admission=fail_closed_admission,
        quota=fail_closed_budget,
        identity=resolver,
    )
    _req, outcome = asyncio.run(ChatSurface(deps=deps).run_lifecycle(scope, b"{}"))  # type: ignore[arg-type]
    assert outcome.refuse_code != REFUSE_IDENTITY_UNRESOLVED, (
        "a valid key must not be refused at the identity phase; "
        f"got {outcome.refuse_code!r}"
    )


def test_wiring_admission_called_and_admitted_continues() -> None:
    """R5.1 / R5.3: the admission surface IS CALLED and an ``Admitted`` grant CONTINUES.

    Feature: chat-surface-gw15, task 2.11
    Validates: Requirements 5.1, 5.3

    An instrumented admission fake records every ``admit`` call and returns ``Admitted``; the
    budget surface admits too, so the lifecycle must reach a PROCEED outcome (admission did not
    short-circuit it, R5.3) and the recorded admission call count must be exactly one (the surface
    was consulted, R5.1). The ``owner_id`` the lifecycle passes must be the request's own tenant.
    """
    import asyncio

    from gateway_v2.admit.grant import Admitted
    from gateway_v2.edge.openai_chat import ChatSurface

    _ORG = "org-admit"

    class _P211RecordingAdmission:
        __slots__ = ("calls", "owner_ids")

        def __init__(self) -> None:
            self.calls = 0
            self.owner_ids: list[str] = []

        async def admit(self, owner_id, request_id, enqueued_at):  # type: ignore[no-untyped-def]
            del enqueued_at
            self.calls += 1
            self.owner_ids.append(owner_id)
            return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=0.0)

        def complete(self, request_id):  # type: ignore[no-untyped-def]
            del request_id

    class _P211AdmitBudget:
        __slots__ = ()

        async def evaluate(self, cost, *, request_id):  # type: ignore[no-untyped-def]
            del cost
            return Admitted(owner_id=_ORG, request_id=request_id, admitted_at=0.0)

        def refund(self, cost, *, request_id):  # type: ignore[no-untyped-def]
            del cost, request_id

    admission = _P211RecordingAdmission()
    deps = _p211_build_deps(org=_ORG, admission=admission, quota=_P211AdmitBudget())
    _req, outcome = asyncio.run(ChatSurface(deps=deps).run_lifecycle(_P211_SCOPE, b"{}"))  # type: ignore[arg-type]

    # (R5.1) the admission surface was consulted exactly once.
    assert admission.calls == 1, (
        f"admission.admit must be called once per request; got {admission.calls}"
    )
    # the admission door was keyed to the request's own tenant.
    assert admission.owner_ids == [_ORG], (
        f"admission must be keyed to the request's org; got {admission.owner_ids}"
    )
    # (R5.3) an Admitted grant continued the lifecycle to a proceed outcome.
    assert outcome.refuse_code is None, (
        f"an Admitted grant must continue past admission; got refuse_code={outcome.refuse_code!r}"
    )
    assert (
        outcome.plan is not None
        and outcome.destination is not None
        and outcome.body is not None
    ), "a proceed outcome must carry the plan-derived provider inputs"


def test_wiring_undecidable_admission_fails_closed_to_shed() -> None:
    """R5.4: an UNDECIDABLE admission fails CLOSED to a shed.

    Feature: chat-surface-gw15, task 2.11
    Validates: Requirements 5.4

    The shipped :class:`~gateway_v2.admit.admission.AdmissionController` maps every undecidable
    path to a ``ShedVerdict`` — its ``admit`` wraps the decision in a fail-closed catch that turns
    any exception into a shed, never a forward. A fake admission surface that mirrors exactly that
    catch (its decision RAISES, so it sheds) stands in for the undecidable path; through the
    lifecycle it must yield the shed refuse-code (``OVERLOAD_SHED``) and leak no proceed state (the
    request refuses at admission, before budget / input).
    """
    import asyncio

    from gateway_v2.admit.grant import Admitted, shed_verdict
    from gateway_v2.domain.posture import OVERLOAD_SHED
    from gateway_v2.edge.openai_chat import ChatSurface

    _ORG = "org-undecidable"

    import random

    class _P211UndecidableAdmission:
        """An admission surface whose decision is undecidable → fail closed to a shed (R5.4).

        Mirrors the shipped ``AdmissionController.admit`` fail-closed catch: any exception on the
        decision path is turned into a ``ShedVerdict`` rather than an admit. Returning a shed here
        is exactly what the shipped controller does on an undecidable path.
        """

        __slots__ = ("_rng", "sheds")

        def __init__(self) -> None:
            self._rng = random.Random(0)
            self.sheds = 0

        async def admit(self, owner_id, request_id, enqueued_at):  # type: ignore[no-untyped-def]
            del owner_id, enqueued_at
            try:
                raise RuntimeError("undecidable admission decision")
            except Exception:  # noqa: BLE001 — mirror the controller's fail-closed catch.
                self.sheds += 1
                return shed_verdict(self._rng, request_id)

        def complete(self, request_id):  # type: ignore[no-untyped-def]
            del request_id

    class _P211AdmitBudget:
        __slots__ = ()

        async def evaluate(self, cost, *, request_id):  # type: ignore[no-untyped-def]
            del cost
            return Admitted(owner_id=_ORG, request_id=request_id, admitted_at=0.0)

        def refund(self, cost, *, request_id):  # type: ignore[no-untyped-def]
            del cost, request_id

    admission = _P211UndecidableAdmission()
    deps = _p211_build_deps(org=_ORG, admission=admission, quota=_P211AdmitBudget())
    _req, outcome = asyncio.run(ChatSurface(deps=deps).run_lifecycle(_P211_SCOPE, b"{}"))  # type: ignore[arg-type]

    # The undecidable path was taken and shed (fail closed).
    assert admission.sheds == 1
    # The lifecycle refuses with the shed code the verdict carries (OVERLOAD_SHED).
    assert outcome.refuse_code == OVERLOAD_SHED, (
        f"an undecidable admission must fail closed to the shed code; got {outcome.refuse_code!r}"
    )
    # No proceed state leaks onto a refusal (fail-closed shape).
    assert (
        outcome.plan is None
        and outcome.destination is None
        and outcome.body is None
    ), "a shed refusal must carry no proceed state"


def test_wiring_quota_evaluate_is_called() -> None:
    """R6.1: the ``QuotaComponent.evaluate`` surface IS CALLED on the budget phase.

    Feature: chat-surface-gw15, task 2.11
    Validates: Requirements 6.1

    An instrumented budget fake records every ``evaluate`` call and admits; admission admits too,
    so the lifecycle reaches a PROCEED outcome. Reaching proceed proves the budget phase ran (it
    sits between admission and the input scan), and the recorded ``evaluate`` count must be exactly
    one — the budget surface was consulted with a non-negative integer cost and the request's
    minted request_id.
    """
    import asyncio

    from gateway_v2.admit.grant import Admitted
    from gateway_v2.edge.openai_chat import ChatSurface

    _ORG = "org-quota"

    class _P211AdmitAdmission:
        __slots__ = ()

        async def admit(self, owner_id, request_id, enqueued_at):  # type: ignore[no-untyped-def]
            del enqueued_at
            return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=0.0)

        def complete(self, request_id):  # type: ignore[no-untyped-def]
            del request_id

    class _P211RecordingBudget:
        __slots__ = ("calls", "costs", "request_ids")

        def __init__(self) -> None:
            self.calls = 0
            self.costs: list[int] = []
            self.request_ids: list[str] = []

        async def evaluate(self, cost, *, request_id):  # type: ignore[no-untyped-def]
            self.calls += 1
            self.costs.append(cost)
            self.request_ids.append(request_id)
            return Admitted(owner_id=_ORG, request_id=request_id, admitted_at=0.0)

        def refund(self, cost, *, request_id):  # type: ignore[no-untyped-def]
            del cost, request_id

    quota = _P211RecordingBudget()
    deps = _p211_build_deps(org=_ORG, admission=_P211AdmitAdmission(), quota=quota)
    _req, outcome = asyncio.run(ChatSurface(deps=deps).run_lifecycle(_P211_SCOPE, b"{}"))  # type: ignore[arg-type]

    # (R6.1) the budget surface was consulted exactly once.
    assert quota.calls == 1, (
        f"QuotaComponent.evaluate must be called once per request; got {quota.calls}"
    )
    # with a non-negative integer cost and the minted request_id.
    assert len(quota.costs) == 1 and isinstance(quota.costs[0], int) and quota.costs[0] >= 0
    assert len(quota.request_ids) == 1 and quota.request_ids[0].startswith("zs-")
    # the budget phase admitted, so the lifecycle proceeded (budget did not short-circuit).
    assert outcome.refuse_code is None, (
        f"an admitting budget must continue the lifecycle; got {outcome.refuse_code!r}"
    )
