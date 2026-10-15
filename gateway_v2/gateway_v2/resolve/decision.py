"""Action->Disposition mapping, transformation building, and the resolver adapter.

GW07 (§10.5.3, C22, Decision 2 / Decision 4 / Decision 5). Pure helpers plus the
frozen ``PlanResolver`` adapter. Every symbol here imports ONLY
``gateway_v2.domain`` (and ``.resolver`` within the resolve package); it never
imports ``egress``, so the "Resolve imports only domain" import-linter contract
stays green.

The redaction sentinel is re-declared LOCALLY (``REDACTION_PLACEHOLDER``) so the
resolve layer need not import ``egress`` to obtain it. It MUST equal the shipped
``gateway_v2.egress.output_guard.REDACTION_PLACEHOLDER`` (``"[REDACTED]"``); a
test pins the agreement without an egress import.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from gateway_v2.domain import (
    Action,
    Decision,
    Disposition,
    ExecutionPlan,
    Finding,
    Phase,
    Transformation,
)

#: The placeholder a redaction ``Transformation`` writes in place of a span.
#: Re-declared locally (resolve imports only domain); MUST equal the shipped
#: ``egress.output_guard.REDACTION_PLACEHOLDER``. A test pins the agreement.
REDACTION_PLACEHOLDER: str = "[REDACTED]"

def _action_to_disposition(action: Action) -> Disposition:
    """Map an ``Action`` to its ``Disposition`` (total over the enum, Decision 2).

    ALLOW->ALLOW, FLAG->FLAG, REDACT->REDACT, BLOCK->BLOCK, REWRITE->REDACT.
    REWRITE is a payload-mutating action with no ``Disposition`` member of its
    own, so it maps to REDACT: the conservative, transformation-producing choice
    that never silently allows and never downgrades a BLOCK. Exhaustive over the
    enum; the final return is the REWRITE arm, keeping it total.
    """
    if action is Action.ALLOW:
        return Disposition.ALLOW
    if action is Action.FLAG:
        return Disposition.FLAG
    if action is Action.REDACT:
        return Disposition.REDACT
    if action is Action.BLOCK:
        return Disposition.BLOCK
    return Disposition.REDACT


def _transformations_for(
    finding: Finding,
    disposition: Disposition,
) -> tuple[Transformation, ...]:
    """Redaction transformations for ``finding`` under its candidate ``disposition``.

    When ``disposition is Disposition.REDACT`` (including REWRITE->REDACT), emit
    one ``Transformation(kind="redact", span, replacement)`` per span in
    ``finding.spans`` using the local ``REDACTION_PLACEHOLDER`` -- mirroring the
    shipped ``MinimalOutputResolver.decide``. Any other disposition contributes
    no transformation.
    """
    if disposition is not Disposition.REDACT:
        return ()
    return tuple(
        Transformation(kind="redact", span=span, replacement=REDACTION_PLACEHOLDER)
        for span in finding.spans
    )


@dataclass(frozen=True, slots=True)
class PlanResolver:
    """Frozen adapter binding a plan+phase to the ``decide(matches)`` surface.

    Structurally compatible with the shipped ``@runtime_checkable``
    ``egress.output_guard.OutputResolver`` protocol (``decide(matches) ->
    Decision``) WITHOUT importing ``egress`` -- the protocol's plan/phase gap is
    closed by binding them at construction (Decision 5). The wiring that injects
    it is GW08/GW15.
    """

    plan: ExecutionPlan
    phase: Phase

    def decide(self, matches: Sequence[Finding]) -> Decision:
        """Resolve ``matches`` against the bound plan+phase via ``resolve``."""
        from gateway_v2.resolve.resolver import resolve

        return resolve(matches, self.plan, self.phase)


__all__ = (
    "REDACTION_PLACEHOLDER",
    "PlanResolver",
    "_action_to_disposition",
    "_transformations_for",
)
