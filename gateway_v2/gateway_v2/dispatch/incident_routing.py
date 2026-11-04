"""GW29 reroute decision: pure, deterministic, single-hop (design 4.5).

`decide_route` turns facts into a `RouteDecision`. The impure reads (kill/isolation status from
`IncidentState`, profiles from the catalog, constraints from the `ConstraintSource`) happen in the
caller; this function has no clock, no random, no I/O and no module state, so the same inputs
always give the same answer and a fallback can never be chosen by anything the caller did not
pass in. `DispatchRouter` (plan-derived upstream selection) is untouched: this decides WHICH MODEL,
that decides WHICH UPSTREAM.

A fallback is eligible only if every rule holds, otherwise the outcome is `REFUSE_REROUTE` (code
`REROUTE_REFUSED`), never a second hop:

1. a fallback is named and differs from the isolated model;
2. it is not killed at any scope for this org;
3. it is not itself actively isolated (single hop);
4. `allowed_models` is unrestricted or contains it;
5. constraints are known and both profiles exist (an unverifiable constraint is a refusal);
6. its compliance tags are a superset of the requested model's plus the required tags;
7. its sensitivity ceiling is at least the requested model's and the request's rank.
"""

from __future__ import annotations

from gateway_v2.domain.incident import (
    ModelProfile,
    ModelStatus,
    RequestConstraints,
    RouteDecision,
    RouteOutcome,
)

__all__ = ("ROUTE_REASONS", "decide_route")

ROUTE_REASONS: tuple[str, ...] = (
    "direct",
    "model_killed",
    "malformed_isolation",
    "no_fallback",
    "fallback_self",
    "fallback_mismatch",
    "fallback_killed",
    "fallback_isolated",
    "fallback_not_allowed",
    "constraints_unknown",
    "profile_missing",
    "compliance_weaker",
    "sensitivity_lower",
    "isolation",
)
"""Closed reasons for a `RouteDecision`. Never request content."""


def _decision(
    outcome: RouteOutcome,
    routed: str | None,
    isolated: str | None,
    fallback: str | None,
    source: str,
    reason: str,
) -> RouteDecision:
    return RouteDecision(outcome, routed, isolated, fallback, source, reason)


def decide_route(
    requested: str,
    requested_status: ModelStatus,
    fallback_status: ModelStatus | None,
    requested_profile: ModelProfile | None,
    fallback_profile: ModelProfile | None,
    constraints: RequestConstraints,
) -> RouteDecision:
    """Decide DIRECT / REROUTE / REFUSE for one request. Pure; see the module docstring."""
    if requested_status.killed:
        return _decision(RouteOutcome.REFUSE_KILLED, None, None, None, "kill", "model_killed")
    isolation = requested_status.isolation
    if isolation is None:
        return _decision(RouteOutcome.DIRECT, requested, None, None, "direct", "direct")
    if isolation.malformed:
        return _decision(
            RouteOutcome.REFUSE_NO_FALLBACK, None, requested, None, "isolation",
            "malformed_isolation",
        )
    named = isolation.fallback
    if named is None or fallback_status is None:
        return _decision(
            RouteOutcome.REFUSE_NO_FALLBACK, None, requested, named, "isolation", "no_fallback",
        )
    refusal = _fallback_refusal(
        requested, named, fallback_status, requested_profile, fallback_profile, constraints,
    )
    if refusal is not None:
        return _decision(
            RouteOutcome.REFUSE_REROUTE, None, requested, named, "isolation", refusal,
        )
    return _decision(RouteOutcome.REROUTE, named, requested, named, "isolation", "isolation")


def _fallback_refusal(
    requested: str,
    named: str,
    status: ModelStatus,
    requested_profile: ModelProfile | None,
    fallback_profile: ModelProfile | None,
    constraints: RequestConstraints,
) -> str | None:
    """The first rule the fallback fails, or None when it is eligible."""
    if named == requested:
        return "fallback_self"
    if status.model != named:
        return "fallback_mismatch"
    if status.killed:
        return "fallback_killed"
    if status.isolation is not None:
        return "fallback_isolated"
    allowed = constraints.allowed_models
    if allowed is not None and named not in allowed:
        return "fallback_not_allowed"
    if not constraints.known:
        return "constraints_unknown"
    if requested_profile is None or fallback_profile is None:
        return "profile_missing"
    if not fallback_profile.compliance_tags >= (
        requested_profile.compliance_tags | constraints.required_tags
    ):
        return "compliance_weaker"
    if fallback_profile.sensitivity_ceiling < max(
        requested_profile.sensitivity_ceiling, constraints.sensitivity_rank,
    ):
        return "sensitivity_lower"
    return None
