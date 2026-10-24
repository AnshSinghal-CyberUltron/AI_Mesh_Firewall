"""Phase-aware output-resolver selection for a route (Group E, Property 11).

The shipped :class:`~gateway_v2.edge.routes.ChatRoute` /
:class:`~gateway_v2.edge.openai_misc.MiscRoute` carry ONE
:class:`~gateway_v2.egress.output_guard.OutputResolver`, used for BOTH the input
scan and the output guard, and the ``decide(matches)`` surface carries no phase.
A plan whose rules are phase-specific (``scope: input`` vs ``scope: output``)
therefore cannot be enforced faithfully through a single resolver: binding it to
one phase silently applies an input-only rule on output, or drops an output-only
rule from input (leak E). The dev profile works around this today by refusing any
non-``both`` plan at start-up (``require_phase_agnostic``).

This module closes leak E by letting a route OPTIONALLY supply a phase-aware
resolver factory:

* :data:`PhaseResolverFactory` -- a ``Callable[[ExecutionPlan, Phase], OutputResolver]``
  that binds a resolver to the request's pinned plan AND a specific phase
  (``Phase.INPUT`` for the input scan, ``Phase.OUTPUT`` for the output guard), so a
  phase-specific rule is enforced only on the phase it is scoped to.
* :func:`resolver_for` -- the pure selector. When the route carries a SET factory
  (not ``None``), it returns ``factory(plan, phase)`` -- the phase-bound resolver.
  When the route carries no factory (the UNSET branch), it returns ``route.resolver``,
  which is byte-identical to today's single-resolver behaviour: the default boot
  (Preservation Req 3.13) and every route built without a factory decide exactly as
  before (Preservation Req 3.5).

The ``phase_resolvers`` attribute is added to the route dataclasses only in a later
task (26.2), so :func:`resolver_for` reads it defensively with ``getattr`` and a
``None`` default -- this module is correct BOTH before 26.2 (no attribute: always the
unset branch) and after (an unwired route defaults the attribute to ``None``, still
the unset branch). The ``route`` argument is a structural duck: it exposes
``.resolver`` and OPTIONALLY ``.phase_resolvers``.

PURE: no I/O, no module-level mutable, no capacity literal.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from gateway_v2.domain.plan import ExecutionPlan, Phase
from gateway_v2.egress.output_guard import OutputResolver

__all__ = (
    "PhaseResolverFactory",
    "resolver_for",
)

#: A factory that binds an :class:`OutputResolver` to a request's pinned
#: :class:`ExecutionPlan` AND a :class:`Phase` (``INPUT`` for the input scan,
#: ``OUTPUT`` for the output guard). A route that carries one of these enforces
#: phase-specific rules correctly; a route that does not falls back to its single
#: ``resolver`` (today's behaviour).
PhaseResolverFactory = Callable[[ExecutionPlan, Phase], OutputResolver]


@runtime_checkable
class _ResolverRoute(Protocol):
    """Structural view of a route for phase-aware resolver selection.

    A route always exposes a single ``resolver`` (today's surface). It MAY also
    expose a ``phase_resolvers`` factory once task 26.2 adds it to the route
    dataclasses; because that attribute is optional, :func:`resolver_for` reads it
    with ``getattr`` rather than naming it on this Protocol (an optional attribute
    is awkward to type structurally), so this view declares only the ``resolver``
    the selector always relies on.
    """

    @property
    def resolver(self) -> OutputResolver: ...


def resolver_for(
    route: _ResolverRoute,
    plan: ExecutionPlan,
    phase: Phase,
) -> OutputResolver:
    """Select the :class:`OutputResolver` a route uses for ``phase`` under ``plan``.

    When the route carries a SET ``phase_resolvers`` factory (not ``None``), return
    ``factory(plan, phase)`` -- a resolver bound to the pinned plan and the phase, so
    an ``input``-scoped rule is enforced only on :attr:`Phase.INPUT` and an
    ``output``-scoped rule only on :attr:`Phase.OUTPUT` (closing leak E).

    When the route carries no factory (the attribute is absent before task 26.2, or
    ``None`` after), return ``route.resolver`` -- byte-identical to today's single
    resolver used for both phases. This is the preservation guarantee: the default
    boot (3.13) and any route built without a factory decide exactly as before (3.5).

    ``phase_resolvers`` is read defensively with ``getattr(route, "phase_resolvers",
    None)`` so this function compiles and is correct both before 26.2 wires the
    attribute and after.
    """
    factory: PhaseResolverFactory | None = getattr(route, "phase_resolvers", None)
    if factory is not None:
        return factory(plan, phase)
    return route.resolver
