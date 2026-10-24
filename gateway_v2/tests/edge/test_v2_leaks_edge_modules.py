"""v2-enforcement-leaks task 25.6: unit tests for the new edge modules.

Example-based unit coverage for the four edge modules landed in Phase 7 (tasks 25.2-25.5),
exercised directly rather than through a full ASGI lifecycle:

* :class:`gateway_v2.edge.request_holds.RequestHolds` -- the per-request hold ledger whose
  :meth:`~gateway_v2.edge.request_holds.RequestHolds.release` must refund (iff spent and not
  dispatched) THEN complete (iff admitted), exactly once behind a latch, synchronously and
  without ever raising (Group D / F; bugfix.md 1.9-1.12, 1.17; Reqs 2.9/2.17).
* :func:`gateway_v2.edge.phase_resolvers.resolver_for` -- the pure phase-aware selector whose
  UNSET branch returns ``route.resolver`` identity and whose SET branch returns
  ``factory(plan, phase)`` (Group E; Req 2.13).
* :func:`gateway_v2.edge.input_enforcement.scan_deltas` -- base-offset span shifting, join-run
  findings and exact-duplicate dropping before the one resolve (Group A; Reqs 2.1-2.3).
* :func:`gateway_v2.edge.embeddings_input.stage_embeddings` -- the embeddings per-item shape
  check that fails closed to ``input_unmaskable`` on a delta-count mismatch, a
  ``VerificationFailure``, or an ``input_is_string`` request with more than one delta (Group A
  embeddings; Reqs 2.19-2.21).

These are TEST fakes (``tests/`` is outside the import-linter layer contract and the
``gateway_v2`` AST / size gates), so they import freely across layers. The ``stage_embeddings``
cases drive the REAL authorize -> verify flow (``resolve.authorize`` ->
``dispatch.authorized.verified_upstream`` -> ``dispatch.verify.apply_and_verify`` + a real
``Transform``) so the shape check runs over real staged bytes; the count-mismatch case injects a
``Transform`` double whose ``to_provider`` packs the wrong number of deltas, and the
``VerificationFailure`` case uses a REDACT decision whose obliged finding span is left uncovered
by any transformation (the dispatch-level precursor of ``input_unmaskable``).
"""

from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType

from gateway_v2.dispatch.provider import UpstreamRequest
from gateway_v2.dispatch.transform import (
    BODY_DELTAS_KEY,
    BODY_MODEL_KEY,
    GatewayRequest,
    Transform,
)
from gateway_v2.domain import (
    Decision,
    Disposition,
    Finding,
    FindingDisposition,
    Span,
)
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import FindingStatus
from gateway_v2.domain.plan import Phase
from gateway_v2.edge.embeddings_input import stage_embeddings
from gateway_v2.edge.input_enforcement import (
    REFUSE_INPUT_SCAN_FAILURE,
    REFUSE_INPUT_UNMASKABLE,
    scan_deltas,
)
from gateway_v2.edge.phase_resolvers import resolver_for
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.wire.openai_misc import MiscEmbeddingsRequest

_PLAN_VERSION = "v2-leaks-25.6"
_DEST = "upstream/default"


# =========================================================================== #
# RequestHolds.release: refund-then-complete, exactly once, never raises.
# =========================================================================== #


class _CountingAdmission:
    """Records every ``complete(request_id)`` call (and can be made to raise)."""

    __slots__ = ("calls", "_raise")

    def __init__(self, *, raise_on_complete: bool = False) -> None:
        self.calls: list[str] = []
        self._raise = raise_on_complete

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> object:  # pragma: no cover - never driven in these unit tests
        del owner_id, request_id, enqueued_at
        raise AssertionError("admit must not be reached by RequestHolds.release")

    def complete(self, request_id: str) -> None:
        self.calls.append(request_id)
        if self._raise:
            raise RuntimeError("boom: complete raised")


class _CountingBudget:
    """Records every ``refund(cost, request_id=...)`` call (and can be made to raise)."""

    __slots__ = ("calls", "_raise")

    def __init__(self, *, raise_on_refund: bool = False) -> None:
        self.calls: list[tuple[int, str]] = []
        self._raise = raise_on_refund

    async def evaluate(
        self, cost: int, *, request_id: str
    ) -> object:  # pragma: no cover - never driven in these unit tests
        del cost, request_id
        raise AssertionError("evaluate must not be reached by RequestHolds.release")

    def refund(self, cost: int, *, request_id: str) -> None:
        self.calls.append((cost, request_id))
        if self._raise:
            raise RuntimeError("boom: refund raised")


def test_release_refunds_then_completes_once_when_spent_and_not_dispatched() -> None:
    """Spent + admitted + NOT dispatched: refund once then complete once; repeats are no-ops.

    **Validates: Requirements 2.9, 2.17**
    """
    holds = RequestHolds()
    holds.admitted("zs-1")
    holds.spent(32, "zs-1")
    admission = _CountingAdmission()
    quota = _CountingBudget()

    holds.release(admission, quota)
    assert quota.calls == [(32, "zs-1")], "refund must run once with (cost, request_id)"
    assert admission.calls == ["zs-1"], "complete must run once with the request_id"

    # A second and third release are no-ops behind the latch.
    holds.release(admission, quota)
    holds.release(admission, quota)
    assert quota.calls == [(32, "zs-1")], "a repeat release must not refund again"
    assert admission.calls == ["zs-1"], "a repeat release must not complete again"


def test_release_does_not_refund_when_dispatched_but_still_completes() -> None:
    """Spent + admitted + dispatched: no refund (provider saw it) but complete still runs.

    **Validates: Requirements 2.9, 2.17**
    """
    holds = RequestHolds()
    holds.admitted("zs-2")
    holds.spent(64, "zs-2")
    holds.mark_dispatched()
    admission = _CountingAdmission()
    quota = _CountingBudget()

    holds.release(admission, quota)
    assert quota.calls == [], "a dispatched spend stands; refund must not run"
    assert admission.calls == ["zs-2"], "an admitted request must still be completed"


def test_release_completes_without_refund_when_admitted_only() -> None:
    """Admitted but nothing spent: complete runs, refund does not.

    **Validates: Requirements 2.9, 2.17**
    """
    holds = RequestHolds()
    holds.admitted("zs-3")
    admission = _CountingAdmission()
    quota = _CountingBudget()

    holds.release(admission, quota)
    assert quota.calls == [], "nothing spent -> no refund"
    assert admission.calls == ["zs-3"], "an admitted request is completed"


def test_release_does_nothing_when_nothing_recorded() -> None:
    """Neither admitted nor spent: release is a total no-op.

    **Validates: Requirements 2.9, 2.17**
    """
    holds = RequestHolds()
    admission = _CountingAdmission()
    quota = _CountingBudget()

    holds.release(admission, quota)
    assert quota.calls == [], "no spend recorded -> no refund"
    assert admission.calls == [], "no admit recorded -> no complete"


def test_release_never_raises_and_still_completes_when_refund_raises() -> None:
    """A refund that raises does not propagate and does not stop the complete call.

    **Validates: Requirements 2.9, 2.17**
    """
    holds = RequestHolds()
    holds.admitted("zs-4")
    holds.spent(16, "zs-4")
    admission = _CountingAdmission()
    quota = _CountingBudget(raise_on_refund=True)

    # release must return None without propagating the refund's RuntimeError.
    assert holds.release(admission, quota) is None
    assert quota.calls == [(16, "zs-4")], "refund was attempted"
    assert admission.calls == ["zs-4"], "a refund raise must not prevent the complete"


def test_release_never_raises_when_complete_raises_after_refund_ran() -> None:
    """A complete that raises does not propagate; the earlier refund still happened.

    **Validates: Requirements 2.9, 2.17**
    """
    holds = RequestHolds()
    holds.admitted("zs-5")
    holds.spent(8, "zs-5")
    admission = _CountingAdmission(raise_on_complete=True)
    quota = _CountingBudget()

    assert holds.release(admission, quota) is None
    assert quota.calls == [(8, "zs-5")], "the refund ran before the complete raised"
    assert admission.calls == ["zs-5"], "complete was attempted (and raised, swallowed)"


# =========================================================================== #
# resolver_for: unset branch returns route.resolver; set branch calls factory.
# =========================================================================== #


class _StubResolver:
    """A trivial ``OutputResolver`` double; identity is all these tests compare."""

    __slots__ = ()

    def decide(self, matches: Sequence[Finding]) -> Decision:  # pragma: no cover - unused
        del matches
        raise AssertionError("decide must not be called by resolver_for")


class _RouteResolverOnly:
    """A route exposing ONLY ``.resolver`` -- no ``phase_resolvers`` attribute at all."""

    __slots__ = ("resolver",)

    def __init__(self, resolver: _StubResolver) -> None:
        self.resolver = resolver


class _RouteWithNoneFactory:
    """A route exposing ``.resolver`` and ``phase_resolvers = None`` (unset-after-26.2 shape)."""

    __slots__ = ("resolver", "phase_resolvers")

    def __init__(self, resolver: _StubResolver) -> None:
        self.resolver = resolver
        self.phase_resolvers = None


class _RouteWithFactory:
    """A route whose ``phase_resolvers`` factory records its ``(plan, phase)`` args."""

    __slots__ = ("resolver", "phase_resolvers", "calls")

    def __init__(self, fallback: _StubResolver, produced: _StubResolver) -> None:
        self.resolver = fallback
        self.calls: list[tuple[object, Phase]] = []

        def _factory(plan: object, phase: Phase) -> _StubResolver:
            self.calls.append((plan, phase))
            return produced

        self.phase_resolvers = _factory


def test_resolver_for_unset_when_attribute_absent_returns_route_resolver() -> None:
    """A route with no ``phase_resolvers`` attribute returns ``route.resolver`` by identity.

    **Validates: Requirements 2.13**
    """
    resolver = _StubResolver()
    route = _RouteResolverOnly(resolver)
    plan = object()

    selected = resolver_for(route, plan, Phase.INPUT)  # type: ignore[arg-type]
    assert selected is resolver, "the attribute-absent branch must return route.resolver identity"


def test_resolver_for_unset_when_factory_is_none_returns_route_resolver() -> None:
    """A route with ``phase_resolvers = None`` returns ``route.resolver`` by identity.

    **Validates: Requirements 2.13**
    """
    resolver = _StubResolver()
    route = _RouteWithNoneFactory(resolver)
    plan = object()

    selected = resolver_for(route, plan, Phase.OUTPUT)  # type: ignore[arg-type]
    assert selected is resolver, "the None-factory branch must return route.resolver identity"


def test_resolver_for_set_factory_is_called_with_plan_and_phase() -> None:
    """A set factory is invoked with exactly ``(plan, phase)`` and its result is returned.

    Both phases route to the one factory call with the matching phase.

    **Validates: Requirements 2.13**
    """
    fallback = _StubResolver()
    produced = _StubResolver()
    route = _RouteWithFactory(fallback, produced)
    plan = object()

    selected_in = resolver_for(route, plan, Phase.INPUT)  # type: ignore[arg-type]
    assert selected_in is produced, "the set-factory branch must return factory(plan, phase)"
    assert route.calls == [(plan, Phase.INPUT)], "factory called once with (plan, Phase.INPUT)"

    selected_out = resolver_for(route, plan, Phase.OUTPUT)  # type: ignore[arg-type]
    assert selected_out is produced
    assert route.calls == [(plan, Phase.INPUT), (plan, Phase.OUTPUT)], (
        "the second call routes Phase.OUTPUT through the same factory"
    )


# =========================================================================== #
# scan_deltas: base-offset shifting, join-run findings, exact-duplicate drop.
# =========================================================================== #


def _finding(
    start: int,
    end: int,
    *,
    detector: str = "det",
    category: Category = Category.PII,
) -> Finding:
    """A minimal EXECUTED finding spanning ``[start, end)``."""
    return Finding(
        detector=detector,
        detector_version="v2-leaks-25.6",
        category=category,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(Span(start=start, end=end),),
        evidence=None,
    )


class _CapturingResolver:
    """An ``OutputResolver`` recording the findings it is handed; returns an ALLOW decision."""

    __slots__ = ("seen",)

    def __init__(self) -> None:
        self.seen: list[Finding] = []

    def decide(self, matches: Sequence[Finding]) -> Decision:
        self.seen.extend(matches)
        return Decision(
            disposition=Disposition.ALLOW,
            per_finding=(),
            transformations=(),
            findings=tuple(matches),
            plan_version=_PLAN_VERSION,
            deciding_rules=(),
            unavailable_detectors=(),
        )


def _substring_detector(needle: str) -> object:
    """A detector flagging every occurrence of ``needle`` in the handed text (local spans)."""

    def _scan(text: str) -> Sequence[Finding]:
        out: list[Finding] = []
        start = text.find(needle)
        while start != -1:
            out.append(_finding(start, start + len(needle)))
            start = text.find(needle, start + 1)
        return tuple(out)

    return _scan


def test_scan_deltas_shifts_spans_by_cumulative_base_offset() -> None:
    """Each delta's findings are shifted into request-content coordinates (running base offset).

    **Validates: Requirements 2.1, 2.3**
    """
    # Two deltas; "SSN" planted in each. Delta 0 text length is 10, so delta 1's base offset is 10.
    deltas = (("user", "pre SSN aa"), ("content_part_text", "xx SSN yy"))
    assert len(deltas[0][1]) == 10
    resolver = _CapturingResolver()

    scan_deltas(
        deltas,
        join_runs=(),
        detector=_substring_detector("SSN"),  # type: ignore[arg-type]
        resolver=resolver,
    )

    spans = sorted((s.start, s.end) for f in resolver.seen for s in f.spans)
    # Delta 0: "SSN" at local offset 4 -> absolute (4, 7). Delta 1: "SSN" at local offset 3,
    # shifted by base 10 -> absolute (13, 16).
    assert spans == [(4, 7), (13, 16)], f"expected shifted spans, got {spans}"


def test_scan_deltas_single_delta_is_identity_shifted() -> None:
    """A single-delta request has base offset 0, so spans are unchanged.

    **Validates: Requirements 2.1, 2.3**
    """
    resolver = _CapturingResolver()
    scan_deltas(
        (("user", "aa SSN bb"),),
        join_runs=(),
        detector=_substring_detector("SSN"),  # type: ignore[arg-type]
        resolver=resolver,
    )
    spans = [(s.start, s.end) for f in resolver.seen for s in f.spans]
    assert spans == [(3, 6)], f"single-delta spans must be identity-shifted, got {spans}"


def test_scan_deltas_join_run_finds_value_split_across_parts() -> None:
    """A value matched only on the JOINED text of two parts is found via the join run.

    The detector matches ``"<a><b>"`` (the two part texts concatenated with the EMPTY
    separator, the request-content coordinate space) but neither part alone. The join run
    ``[(0, 1)]`` scans the joined text; the finding's span is shifted by delta 0's base
    offset (0 here). Confirms the separator is the empty string, byte-identical to
    ``dispatch/verify._request_content``'s ``"".join(...)`` so a straddling span aligns.

    **Validates: Requirements 2.1, 2.3**
    """
    part_a = "my secret is "
    part_b = "hunter2 ok"
    joined = part_a + part_b  # the separator-free concatenation (coordinate space)

    def _joined_only_detector(text: str) -> Sequence[Finding]:
        if text != joined:
            return ()  # neither part alone matches
        start = text.index("secret is hunter2")
        return (_finding(start, start + len("secret is hunter2"), category=Category.SECRET),)

    resolver = _CapturingResolver()
    scan_deltas(
        (("content_part_text", part_a), ("content_part_text", part_b)),
        join_runs=((0, 1),),
        detector=_joined_only_detector,  # type: ignore[arg-type]
        resolver=resolver,
    )

    spans = [(s.start, s.end) for f in resolver.seen for s in f.spans]
    expected_start = joined.index("secret is hunter2")
    assert spans == [(expected_start, expected_start + len("secret is hunter2"))], (
        f"the join-run finding (shifted by delta 0 base 0) must be present, got {spans}"
    )


def test_scan_deltas_drops_exact_duplicate_from_delta_and_join_run() -> None:
    """A value seen both in a part and in the join run is resolved exactly once.

    The detector flags ``needle`` wherever it appears. In part 0 it appears at local offset 0
    (absolute 0..6); the join run "needle <b>" also contains it at offset 0 (shifted by delta 0
    base 0 -> absolute 0..6). The two findings are span-identical, so the resolver sees it once.
    The detector is invoked once per delta plus once per join run (2 + 1 = 3).

    **Validates: Requirements 2.1, 2.3**
    """
    needle = "needle"
    part_a = "needle"  # exactly the needle, local span 0..6
    part_b = "tail"
    invocations: list[str] = []

    def _needle_detector(text: str) -> Sequence[Finding]:
        out: list[Finding] = []
        start = text.find(needle)
        while start != -1:
            out.append(_finding(start, start + len(needle)))
            start = text.find(needle, start + 1)
        return tuple(out)

    resolver = _CapturingResolver()
    scan_deltas(
        (("content_part_text", part_a), ("content_part_text", part_b)),
        join_runs=((0, 1),),
        detector=_needle_detector,  # type: ignore[arg-type]
        resolver=resolver,
        on_invoke=invocations.append,
    )

    spans = [(s.start, s.end) for f in resolver.seen for s in f.spans]
    assert spans == [(0, 6)], (
        f"the span-identical duplicate must be dropped to one finding, got {spans}"
    )
    assert invocations == [part_a, part_b, part_a + part_b], (
        f"on_invoke fires once per delta plus once per join run with exact text, got {invocations}"
    )


def test_scan_deltas_on_invoke_sees_exact_per_delta_texts() -> None:
    """on_invoke is called once per detector run with the exact scanned text (no join runs).

    **Validates: Requirements 2.1, 2.3**
    """
    invocations: list[str] = []
    resolver = _CapturingResolver()
    scan_deltas(
        (("user", "alpha"), ("user", "beta")),
        join_runs=(),
        detector=_substring_detector("zzz"),  # type: ignore[arg-type]  (matches nothing)
        resolver=resolver,
        on_invoke=invocations.append,
    )
    assert invocations == ["alpha", "beta"], f"on_invoke per-delta texts, got {invocations}"
    assert resolver.seen == [], "no match -> no findings resolved"


def test_scan_deltas_detector_raise_fails_closed() -> None:
    """A detector that raises maps to scan_failure with no decision (fail closed, Property 4)."""

    def _raising_detector(text: str) -> Sequence[Finding]:
        del text
        raise RuntimeError("detector boom")

    result = scan_deltas(
        (("user", "hi"),),
        join_runs=(),
        detector=_raising_detector,  # type: ignore[arg-type]
        resolver=_CapturingResolver(),
    )
    assert result.refuse_code == REFUSE_INPUT_SCAN_FAILURE
    assert result.decision is None


def test_scan_deltas_resolver_raise_fails_closed() -> None:
    """A resolver that raises maps to scan_failure with no decision (fail closed, Property 4)."""

    class _RaisingResolver:
        __slots__ = ()

        def decide(self, matches: Sequence[Finding]) -> Decision:
            del matches
            raise RuntimeError("resolver boom")

    result = scan_deltas(
        (("user", "hi"),),
        join_runs=(),
        detector=_substring_detector("zzz"),  # type: ignore[arg-type]
        resolver=_RaisingResolver(),
    )
    assert result.refuse_code == REFUSE_INPUT_SCAN_FAILURE
    assert result.decision is None


# =========================================================================== #
# stage_embeddings: count mismatch, VerificationFailure, input_is_string>1 -> unmaskable.
# =========================================================================== #


def _allow_decision(findings: tuple[Finding, ...] = ()) -> Decision:
    """A clean ALLOW decision (no transformations) that flows through verify unchanged."""
    return Decision(
        disposition=Disposition.ALLOW,
        per_finding=(),
        transformations=(),
        findings=findings,
        plan_version=_PLAN_VERSION,
        deciding_rules=(),
        unavailable_detectors=(),
    )


def _unverifiable_redact_decision(span: Span) -> Decision:
    """A REDACT decision whose obliged finding span is covered by NO transformation.

    ``dispatch.verify.apply_and_verify`` obliges this finding's span (finding-index keyed,
    REDACT) but finds no transformation covering it, so ``_assert_fully_covered_by_transformations``
    raises ``VerificationFailure`` -- the dispatch precursor of ``input_unmaskable`` with zero
    provider calls.
    """
    finding = Finding(
        detector="det",
        detector_version="v2-leaks-25.6",
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(span,),
        evidence=None,
    )
    return Decision(
        disposition=Disposition.REDACT,
        per_finding=(
            FindingDisposition(
                detector="det", disposition=Disposition.REDACT, finding_index=0,
            ),
        ),
        transformations=(),  # nothing covers the obliged span -> fail closed
        findings=(finding,),
        plan_version=_PLAN_VERSION,
        deciding_rules=("det",),
        unavailable_detectors=(),
    )


class _DroppingTransform:
    """A Transform double whose ``to_provider`` packs FEWER deltas than the request carried.

    It drops the last delta so the staged count differs from ``len(req.input_items)``, driving
    ``stage_embeddings``'s post-stage shape check to ``input_unmaskable`` on a count mismatch.
    """

    __slots__ = ()

    def to_provider(self, req: GatewayRequest) -> UpstreamRequest:
        dropped = req.text_deltas[:-1]
        body: MappingProxyType[str, object] = MappingProxyType(
            {BODY_MODEL_KEY: req.model, BODY_DELTAS_KEY: dropped}
        )
        return UpstreamRequest(destination=req.destination, body=body)


def _embeddings_req(items: tuple[str, ...], *, is_string: bool = False) -> MiscEmbeddingsRequest:
    return MiscEmbeddingsRequest(model="echo-1", input_items=items, input_is_string=is_string)


def test_stage_embeddings_happy_path_returns_upstream_with_matching_shape() -> None:
    """A clean ALLOW embeddings request stages an UpstreamRequest with one delta per item, in order.

    **Validates: Requirements 2.19, 2.20**
    """
    req = _embeddings_req(("release notes", "quarterly plan"))
    staged = stage_embeddings(
        _allow_decision(),
        req,
        destination=_DEST,
        transform=Transform(),
    )
    assert isinstance(staged, UpstreamRequest), (
        "a clean ALLOW must stage upstream bytes, not refuse"
    )
    raw = staged.body.get(BODY_DELTAS_KEY, ())
    assert isinstance(raw, tuple)
    assert raw == (
        ("embeddings_input", "release notes"),
        ("embeddings_input", "quarterly plan"),
    ), f"one embeddings_input delta per item, in order, got {raw!r}"


def test_stage_embeddings_count_mismatch_returns_input_unmaskable() -> None:
    """A staged body with the wrong number of deltas fails the shape check -> input_unmaskable.

    The injected ``_DroppingTransform`` packs one fewer delta than the request's two items.

    **Validates: Requirements 2.21**
    """
    req = _embeddings_req(("first item", "second item"))
    staged = stage_embeddings(
        _allow_decision(),
        req,
        destination=_DEST,
        transform=_DroppingTransform(),  # type: ignore[arg-type]
    )
    assert staged == REFUSE_INPUT_UNMASKABLE, (
        f"a staged delta count != len(input_items) must fail closed, got {staged!r}"
    )


def test_stage_embeddings_verification_failure_returns_input_unmaskable() -> None:
    """A REDACT decision whose obliged span is uncovered raises VerificationFailure -> unmaskable.

    The real ``apply_and_verify`` (via ``stage_upstream``/``verified_upstream``) raises because the
    obliged finding span is covered by no transformation; ``stage_upstream`` maps that
    ``VerificationFailure`` to ``input_unmaskable``, which ``stage_embeddings`` propagates.

    **Validates: Requirements 2.21**
    """
    # One item whose text is long enough to carry the obliged span in content coordinates.
    req = _embeddings_req(("my ssn is 123-45-6789 ok",))
    span = Span(start=10, end=21)  # "123-45-6789" within the single item text
    staged = stage_embeddings(
        _unverifiable_redact_decision(span),
        req,
        destination=_DEST,
        transform=Transform(),
    )
    assert staged == REFUSE_INPUT_UNMASKABLE, (
        f"an unverifiable REDACT must fail closed to input_unmaskable, got {staged!r}"
    )


def test_stage_embeddings_string_input_with_more_than_one_delta_returns_input_unmaskable() -> None:
    """An ``input_is_string`` request that stages more than one delta fails the shape check.

    **Validates: Requirements 2.20, 2.21**
    """
    # input_is_string=True but two items -> two staged deltas -> shape clause 3 fails.
    req = _embeddings_req(("alpha", "beta"), is_string=True)
    staged = stage_embeddings(
        _allow_decision(),
        req,
        destination=_DEST,
        transform=Transform(),
    )
    assert staged == REFUSE_INPUT_UNMASKABLE, (
        f"input_is_string with >1 staged delta must fail closed, got {staged!r}"
    )
