"""Force-release trade-off mirror and pure redaction folding (R2-06 / GW12b, task 9.1).

The per-pattern ``Trade_Off_Outcome`` is authoritative in
``detect.holdback.TRADE_OFF``, but ``egress`` sits BELOW ``detect`` in the
import-linter layer contract and MUST NOT import it. This module is the single
egress-side mirror: it derives a finding's declared outcome from the resolver's
``Decision`` disposition (``BLOCK`` -> terminate-the-stream, ``REDACT`` ->
redact-the-remainder), records the two legal outcome names, and folds a
force-release match's still-held remainder spans into the output ``Decision`` as
redactions so they are applied in the same single pass as the normal decision.

Layering: imports only from ``gateway_v2.domain``. A test
(``tests/egress/test_lgw12b_stream.py``) imports ``detect.holdback.TRADE_OFF``
and pins that the egress-derived outcome agrees with it for every class.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from gateway_v2.domain import (
    Decision,
    Disposition,
    Finding,
    FindingDisposition,
    Span,
    Transformation,
)

__all__ = (
    "TRADE_OFF_OUTCOMES",
    "TRADE_OFF_REDACT_REMAINDER",
    "TRADE_OFF_TERMINATE",
    "ForcedReleaseTerminate",
    "completed_in",
    "derive_trade_off_outcome",
    "longest_unbroken_run_bytes",
    "with_remainder_redactions",
)

#: The default over-cap outcome: redact every still-held remainder byte (R6.3).
TRADE_OFF_REDACT_REMAINDER: Final = "redact_remainder"
#: The alternative over-cap outcome: terminate the stream (R6.4).
TRADE_OFF_TERMINATE: Final = "terminate"
#: The complete, closed set of legal trade-off outcomes (R6.1). An over-cap held
#: class whose derived outcome is not one of these fails closed (R6.6).
TRADE_OFF_OUTCOMES: Final[tuple[str, ...]] = (
    TRADE_OFF_REDACT_REMAINDER,
    TRADE_OFF_TERMINATE,
)

#: The placeholder a force-release remainder redaction writes. Mirrors
#: ``output_guard.REDACTION_PLACEHOLDER``; kept local so this module imports only
#: ``domain``.
_REMAINDER_PLACEHOLDER: Final = "[REDACTED]"


class ForcedReleaseTerminate(Exception):
    """Internal: a force-release trade-off whose declared outcome is terminate.

    Converted to the ``forced_release_tradeoff`` terminal frame by the pipeline
    (R6.4). Not public vocabulary -- callers see only the terminal error code.
    """

    def __init__(self, detector: str) -> None:
        super().__init__(f"forced-release trade-off terminate (class={detector})")
        self.detector = detector


def derive_trade_off_outcome(decision: Decision) -> str | None:
    """Derive a single finding's declared ``Trade_Off_Outcome`` from its decision.

    The resolver mirrors ``detect.holdback.TRADE_OFF``: a BLOCK disposition means
    terminate-the-stream, a REDACT disposition means redact-the-remainder. Any
    other disposition (ALLOW/FLAG) has no declared outcome, so this returns
    ``None`` and the caller fails closed (``undefined_tradeoff``, R6.6).
    """
    if decision.disposition is Disposition.BLOCK:
        return TRADE_OFF_TERMINATE
    if decision.disposition is Disposition.REDACT:
        return TRADE_OFF_REDACT_REMAINDER
    return None


def with_remainder_redactions(
    decision: Decision,
    remainder_edits: tuple[tuple[int, int], ...],
) -> Decision:
    """Fold force-release remainder spans into ``decision`` as redactions (R6.3).

    Each ``(start, end)`` is an ABSOLUTE span of a still-held match remainder to
    replace with the placeholder before any byte of it releases. The spans are
    added as redact ``Transformation``s so they apply in the same single pass as
    the normal decision -- offsets stay absolute, no byte is transformed twice. A
    BLOCK decision is returned unchanged (``apply_decision`` raises before any
    redaction runs). An edit matching an existing span exactly is de-duplicated;
    ``apply_decision`` rejects a true overlap, failing closed.
    """
    if not remainder_edits or decision.disposition is Disposition.BLOCK:
        return decision
    existing: set[tuple[int, int]] = {
        (t.span.start, t.span.end) for t in decision.transformations
    }
    extra: list[Transformation] = [
        Transformation(kind="redact", span=Span(start, end), replacement=_REMAINDER_PLACEHOLDER)
        for start, end in remainder_edits
        if (start, end) not in existing
    ]
    if not extra:
        return decision
    # The remainder redaction raises the effective disposition to at least
    # REDACT; keep per_finding consistent so Decision.__post_init__'s
    # most-restrictive invariant holds.
    per_finding = decision.per_finding
    has_restriction = any(
        fd.disposition in (Disposition.REDACT, Disposition.BLOCK) for fd in per_finding
    )
    if not has_restriction:
        per_finding = (
            *per_finding,
            FindingDisposition(detector="forced_release_remainder", disposition=Disposition.REDACT),
        )
    return Decision(
        disposition=Disposition.REDACT,
        per_finding=per_finding,
        transformations=(*decision.transformations, *extra),
        findings=decision.findings,
        plan_version=decision.plan_version,
        deciding_rules=decision.deciding_rules,
        unavailable_detectors=decision.unavailable_detectors,
    )


def longest_unbroken_run_bytes(s: str) -> int:
    """Length in bytes of the longest run of non-whitespace in ``s`` (R5.5)."""
    best = 0
    run = 0
    for ch in s:
        if ch.isspace():
            run = 0
        else:
            run += 1
            if run > best:
                best = run
    return best


def completed_in(
    hits: Sequence[Finding],
    base_offset: int,
    length: int,
) -> tuple[Finding, ...]:
    """Findings whose whole span lies within the released slice (task 9.1).

    Empty ``hits`` (thin harness) returns ``()`` -> an ALLOW decision.
    """
    if not hits:
        return ()
    end = base_offset + length
    return tuple(
        f
        for f in hits
        if f.spans and all(base_offset <= s.start and s.end <= end for s in f.spans)
    )
