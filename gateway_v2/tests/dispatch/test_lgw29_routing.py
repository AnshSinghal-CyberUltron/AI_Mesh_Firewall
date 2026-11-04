# Validates: Requirements 4 (reroute eligibility rules 1-7, single hop, pure decision)
"""One example per `decide_route` rule, plus the malformed and direct paths."""

from __future__ import annotations

import pytest

from gateway_v2.dispatch.incident_routing import ROUTE_REASONS, decide_route
from gateway_v2.domain.incident import RouteOutcome
from tests.dispatch._lgw29_support import constraints, iso, profile, status

A, B = "model-a", "model-b"
TAGS = frozenset({"hipaa"})


def _route(
    *,
    a: object = None,
    b: object = "default",
    pa: object = "default",
    pb: object = "default",
    c: object = None,
):
    a_status = a if a is not None else status(A, isolation=iso(A, B))
    b_status = status(B) if b == "default" else b
    p_a = profile(A, TAGS, 2) if pa == "default" else pa
    p_b = profile(B, TAGS, 2) if pb == "default" else pb
    return decide_route(A, a_status, b_status, p_a, p_b, c if c is not None else constraints())  # type: ignore[arg-type]


def test_not_isolated_is_direct() -> None:
    d = decide_route(A, status(A), None, None, None, constraints(known=False))
    assert (d.outcome, d.routed_model, d.isolated_model) == (RouteOutcome.DIRECT, A, None)


def test_killed_requested_model_is_refused_even_when_a_fallback_is_named() -> None:
    d = decide_route(A, status(A, killed=True, isolation=iso(A, B)), status(B), None, None,
                     constraints())
    assert d.outcome is RouteOutcome.REFUSE_KILLED and d.routed_model is None


def test_eligible_fallback_reroutes() -> None:
    d = _route()
    assert (d.outcome, d.routed_model, d.isolated_model, d.fallback) == (
        RouteOutcome.REROUTE, B, A, B)
    assert d.decision_source == "isolation" and d.reason == "isolation"


def test_isolated_without_fallback_is_refused() -> None:
    d = _route(a=status(A, isolation=iso(A, None)), b=None)
    assert d.outcome is RouteOutcome.REFUSE_NO_FALLBACK and d.reason == "no_fallback"


def test_malformed_isolation_refuses_that_model_only() -> None:
    d = _route(a=status(A, isolation=iso(A, B, malformed=True)))
    assert d.outcome is RouteOutcome.REFUSE_NO_FALLBACK and d.reason == "malformed_isolation"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        # rule 1: fallback equals the isolated model, or does not match the named one
        ({"a": status(A, isolation=iso(A, A)), "b": status(A)}, "fallback_self"),
        ({"b": status("model-c")}, "fallback_mismatch"),
        # rule 2: fallback killed
        ({"b": status(B, killed=True)}, "fallback_killed"),
        # rule 3: fallback itself isolated (no second hop)
        ({"b": status(B, isolation=iso(B, "model-c"))}, "fallback_isolated"),
        # rule 4: not in allowed_models
        ({"c": constraints(allowed=frozenset({A}))}, "fallback_not_allowed"),
        # rule 5: unknown constraints, missing profiles
        ({"c": constraints(known=False)}, "constraints_unknown"),
        ({"pa": None}, "profile_missing"),
        ({"pb": None}, "profile_missing"),
        # rule 6: weaker compliance, including required request tags
        ({"pb": profile(B, frozenset(), 2)}, "compliance_weaker"),
        ({"c": constraints(required=frozenset({"pci"}))}, "compliance_weaker"),
        # rule 7: lower sensitivity ceiling, including the request's own rank
        ({"pb": profile(B, TAGS, 1)}, "sensitivity_lower"),
        ({"c": constraints(rank=3)}, "sensitivity_lower"),
    ],
)
def test_each_rule_refuses_the_reroute(kwargs: dict[str, object], reason: str) -> None:
    d = _route(**kwargs)
    assert d.outcome is RouteOutcome.REFUSE_REROUTE and d.routed_model is None
    assert d.reason == reason and d.isolated_model == A


def test_equal_or_stronger_fallback_is_accepted() -> None:
    d = _route(pb=profile(B, TAGS | {"extra"}, 5), c=constraints(required=TAGS, rank=2))
    assert d.outcome is RouteOutcome.REROUTE


def test_allowed_models_containing_the_fallback_passes() -> None:
    assert _route(c=constraints(allowed=frozenset({B}))).outcome is RouteOutcome.REROUTE


def test_every_reason_is_in_the_closed_vocabulary() -> None:
    for kwargs in ({}, {"b": None}, {"b": status(B, killed=True)}, {"pa": None}):
        assert _route(**kwargs).reason in ROUTE_REASONS
