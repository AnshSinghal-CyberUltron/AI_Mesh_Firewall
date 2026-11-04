"""LGW33-3 (offline equivalent, not the live gate): an ungrounded answer on a RAG request is flagged
per the org's hallucination action, and the path makes zero firewall-attributable provider calls."""
from __future__ import annotations

from collections.abc import Sequence

from gateway_v2.detect.guard.backend import Budget, GuardResult
from gateway_v2.domain import Action, Category, GroundingMode, StreamingMode
from gateway_v2.runtime.kinds import CapacityHint
from tests.e2e.gw33_support import plan_of, rule, run

_CONTEXT = ("Our refund policy allows returns within thirty days of purchase. "
            "Shipping takes five business days for domestic orders.",)
_GOOD = "Our refund policy allows returns within thirty days of purchase."
_BAD = "Purple elephants orchestrate quantum bakeries throughout Antarctica every Tuesday."


def _plan(mode: StreamingMode, action: Action, klass: str = "output.grounding.lexical"):  # type: ignore[no-untyped-def]
    return plan_of(mode, rule("out_ground", Category.HALLUCINATION, action, klass, threshold=0.3,
                              template="[unsupported claim]"))


def test_flag_marks_the_response_for_review_and_changes_no_byte() -> None:
    for mode in StreamingMode:
        result = run(_plan(mode, Action.FLAG), [("output_content", f"{_GOOD} {_BAD}")],
                     context=_CONTEXT)
        assert result.released == f"{_GOOD} {_BAD}"
        assert result.stats.review_required is True  # type: ignore[attr-defined]


def test_grounded_answers_and_requests_without_context_are_not_flagged() -> None:
    for mode in StreamingMode:
        assert run(_plan(mode, Action.FLAG), [("output_content", _GOOD)],
                   context=_CONTEXT).stats.review_required is False  # type: ignore[attr-defined]
        assert run(_plan(mode, Action.FLAG), [("output_content", _BAD)]
                   ).stats.review_required is False  # type: ignore[attr-defined]


def test_strict_redact_rewrite_and_block_apply_to_the_ungrounded_claim() -> None:
    answer = f"{_GOOD} {_BAD}"
    redact = run(_plan(StreamingMode.STRICT_WITHHOLD, Action.REDACT), [("output_content", answer)],
                 context=_CONTEXT)
    assert _BAD not in redact.released and _GOOD in redact.released
    rewrite = run(_plan(StreamingMode.STRICT_WITHHOLD, Action.REWRITE), [("output_content", answer)],
                  context=_CONTEXT)
    assert "[unsupported claim]" in rewrite.released and _BAD not in rewrite.released
    block = run(_plan(StreamingMode.STRICT_WITHHOLD, Action.BLOCK), [("output_content", answer)],
                context=_CONTEXT)
    assert block.released == "" and block.codes == ["output_blocked"]


class _LocalBackend:
    """A local capable stub: counts calls; there is no provider anywhere in this path."""

    supported_categories = frozenset({Category.HALLUCINATION})
    calls = 0

    async def classify(self, windows: Sequence[str], budget: Budget) -> Sequence[GuardResult]:
        type(self).calls += 1
        return [GuardResult(i, "hallucination", 0.9) for i in range(len(windows))]

    async def readiness(self) -> object:  # pragma: no cover
        raise NotImplementedError

    def capacity_hint(self) -> CapacityHint:  # pragma: no cover
        return CapacityHint(tokens_per_second=1.0)


def test_model_mode_runs_on_the_local_backend_only_and_unavailable_routes_by_posture() -> None:
    klass = f"output.grounding.{GroundingMode.MODEL.value}:local-halu"
    backend = _LocalBackend()
    result = run(_plan(StreamingMode.STRICT_WITHHOLD, Action.FLAG, klass),
                 [("output_content", f"{_GOOD} {_BAD}")], context=_CONTEXT, backend=backend)
    assert backend.calls == 1 and result.stats.review_required is True  # type: ignore[attr-defined]
    # No backend advertises hallucination: UNAVAILABLE, FAIL_CLOSED + block => the response is withheld.
    closed = run(_plan(StreamingMode.STRICT_WITHHOLD, Action.BLOCK, klass),
                 [("output_content", _GOOD)], context=_CONTEXT, backend=None)
    assert closed.released == "" and closed.codes == ["output_blocked"]
