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
    Rule,
    Surface,
    Transformation,
)
from gateway_v2.resolve.rewrite import render_template

#: The placeholder a redaction ``Transformation`` writes in place of a span.
#: Re-declared locally (resolve imports only domain); MUST equal the shipped
#: ``egress.output_guard.REDACTION_PLACEHOLDER``. A test pins the agreement.
REDACTION_PLACEHOLDER: str = "[REDACTED]"

def _action_to_disposition(action: Action) -> Disposition:
    """Map an ``Action`` to its ``Disposition`` (total over the enum, Decision 2).

    ALLOW->ALLOW, FLAG->FLAG, REDACT->REDACT, REWRITE->REWRITE, BLOCK->BLOCK
    (GW33: REWRITE is a first-class disposition, ranked between REDACT and BLOCK, so it never
    downgrades a BLOCK and never silently allows). Exhaustive over the enum; the final return is
    the REWRITE arm, keeping it total.
    """
    if action is Action.ALLOW:
        return Disposition.ALLOW
    if action is Action.FLAG:
        return Disposition.FLAG
    if action is Action.REDACT:
        return Disposition.REDACT
    if action is Action.BLOCK:
        return Disposition.BLOCK
    return Disposition.REWRITE


def _transformations_for(
    finding: Finding,
    disposition: Disposition,
    rule: Rule | None = None,
) -> tuple[Transformation, ...]:
    """Edits for ``finding`` under its candidate ``disposition``.

    REDACT emits one ``Transformation(kind="redact", span, replacement)`` per span using the
    local ``REDACTION_PLACEHOLDER`` -- mirroring the shipped ``MinimalOutputResolver.decide``.
    REWRITE (GW33) emits ``kind="rewrite"`` with the deciding ``rule``'s declared template rendered
    by ``render_template``. Any other disposition contributes no transformation.
    """
    if disposition is Disposition.REWRITE and rule is not None:
        replacement = render_template(rule.rewrite_template, finding.category, rule.rule_id)
        return tuple(
            Transformation(kind="rewrite", span=span, replacement=replacement)
            for span in finding.spans
        )
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
    #: GW26. ``None`` selects by phase only. A set surface keeps rules that omit it.
    surface: Surface | None = None
    #: GW26. ``None`` skips rules that name a pipeline stage.
    stage: str | None = None

    def decide(self, matches: Sequence[Finding]) -> Decision:
        """Resolve ``matches`` against the bound plan, phase, surface, and stage."""
        from gateway_v2.resolve.resolver import resolve

        return resolve(matches, self.plan, self.phase, self.surface, self.stage)


__all__ = (
    "REDACTION_PLACEHOLDER",
    "PlanResolver",
    "_action_to_disposition",
    "_transformations_for",
)
