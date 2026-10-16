"""The one byte-verified redactor + reusable verify (GW09, §10.9, R5).

This is the SINGLE detect-layer redactor GW09 ships (design "redactor.py", R5).
It removes the v1 "two redactors, one wire" defect: v1 DECIDED REDACT against
``INPUT_SCANNER.redact_pii`` on a flattened prompt while the wire was produced
by a different, "by construction WEAKER" ``patterns.redact_all`` -- so the
system could decide REDACT and emit bytes the decision never inspected. Here
there is exactly ONE redactor whose output is BYTE-VERIFIED against the finding
spans: a redaction that did not remove what it claimed is a FAILURE
(fail-closed), not a pass (R5.2/R5.3).

Reconciliation with the GW07 resolver and egress (no duplicate redactor):

* GW09 detectors emit ``Finding[]`` with byte-offset spans into the inspected
  text. GW09 does NOT build ``Transformation``s and does NOT decide disposition.
* The GW07 resolver (``resolve/decision._transformations_for``, shipped) maps a
  REDACT disposition to ``Transformation(kind="redact", span, [REDACTED])``,
  one per finding span.
* :func:`redact` here CONSUMES those ``Transformation``s and is the single
  detect-layer byte authority: it produces the transformed bytes AND the
  span-accurate byte-verify. It mirrors ``egress.output_guard.apply_decision``'s
  span-application + overlap semantics (collect in-range spans, SORT by start,
  compose left-to-right, FAIL CLOSED on a true overlap).
* :func:`verify` is the reusable fail-closed check GW11's dispatch-layer
  re-verify (before the provider call) imports and reuses (R5.5/R11.2).

There is exactly ONE redactor (this module) and exactly one ``verify``.

Fail-closed on overlap -- design choice. ``egress.apply_decision`` RAISES
``ValueError`` on overlapping spans. Here the design wants a ``RedactResult``
carrying ``verified`` / ``unmaskable_spans`` so the Unmaskable_Path is
DATA-DRIVEN rather than exception-driven (and so ``redact`` never raises out of
the detect layer, keeping the no-raise AST gate green). So on a true overlap --
and on any span that cannot be safely covered (out of range) -- :func:`redact`
does NOT raise: it records the offending span(s) in ``unmaskable_spans`` and
returns ``verified=False``. The overlap/coverage SEMANTICS match egress
one-for-one; only the signalling differs (a value, not an exception).

Layering: imports only ``gateway_v2.domain`` (for ``Transformation``, ``Span``)
+ stdlib. The ``[REDACTED]`` placeholder is re-declared locally (detect may not
import ``egress``) and pinned equal to ``egress.output_guard.REDACTION_PLACEHOLDER``
and ``canonicalize.REDACTION_PLACEHOLDER`` by a test. Mirrors the house idiom:
``from __future__ import annotations``, frozen ``@dataclass(frozen=True,
slots=True)``, tuple ``__all__``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from gateway_v2.domain import Span, Transformation

__all__ = (
    "REDACTION_PLACEHOLDER",
    "RedactResult",
    "redact",
    "verify",
)

#: Re-declared locally (detect may not import ``egress``); a test pins this
#: equal to ``egress.output_guard.REDACTION_PLACEHOLDER`` and to
#: ``canonicalize.REDACTION_PLACEHOLDER``.
REDACTION_PLACEHOLDER: str = "[REDACTED]"


@dataclass(frozen=True, slots=True)
class RedactResult:
    """Outcome of :func:`redact`: the transformed payload + the byte-verify.

    ``payload`` is the transformed text (every applicable span replaced).
    ``verified`` is True iff every targeted span's ORIGINAL bytes are absent
    from ``payload`` (R5.2). ``unmaskable_spans`` lists the spans a safe
    transform could not cover (out of range, a true overlap, or a no-op whose
    bytes survive) -- a non-empty tuple always pairs with ``verified=False``
    and drives the Unmaskable_Path (R5.3/R5.4).
    """

    payload: str
    verified: bool
    unmaskable_spans: tuple[Span, ...]


def _in_range(span: Span, length: int) -> bool:
    """Whether ``span`` lies wholly inside ``[0, length)`` (mirrors egress ``_covers``)."""
    return 0 <= span.start <= span.end <= length


def redact(text: str, transformations: Sequence[Transformation]) -> RedactResult:
    """Apply ``transformations`` over ``text`` and byte-verify the result (R5.2).

    Each ``Transformation``'s span is replaced by its ``replacement``. In-range
    spans are collected, SORTED by start, and composed left-to-right -- exactly
    ``egress.output_guard.apply_decision``'s span-application semantics. A span
    out of range, or a true overlap of two spans, is UNMASKABLE: it is recorded
    in ``unmaskable_spans`` and does not corrupt the composed payload (the
    overlapping span is skipped so composition stays well-defined).

    After applying, the payload is BYTE-VERIFIED (R5.2): for every TARGETED span
    whose original bytes ``text[span.start:span.end]`` are non-empty, those bytes
    must be absent from the resulting payload. A span that survives -- e.g. a
    no-op whose ``replacement`` equals the original slice, or a span that could
    not be covered -- is recorded in ``unmaskable_spans`` and forces
    ``verified=False`` (fail-closed, R5.3/R5.4).

    Returns a :class:`RedactResult`; never raises out of the detect layer.
    """
    length = len(text)

    # Collect in-range edits; out-of-range spans are unmaskable up front.
    edits: list[tuple[int, int, str]] = []
    unmaskable: list[Span] = []
    for transformation in transformations:
        span = transformation.span
        if not _in_range(span, length):
            unmaskable.append(span)
            continue
        edits.append((span.start, span.end, transformation.replacement))

    # Sort by (start, end) so replacements compose and overlaps are detectable
    # deterministically -- mirrors apply_decision. On a true overlap, the later
    # span is unmaskable (fail-closed) rather than raising.
    edits.sort(key=lambda item: (item[0], item[1]))
    out: list[str] = []
    cursor = 0
    for start, end, replacement in edits:
        if start < cursor:
            # True overlap with an already-applied span: fail closed as data.
            unmaskable.append(Span(start=start, end=end))
            continue
        out.append(text[cursor:start])
        out.append(replacement)
        cursor = end
    out.append(text[cursor:])
    payload = "".join(out)

    # Byte-verify every targeted span against the composed payload (R5.2). A
    # span whose original bytes survive -- a no-op, or an unmaskable span never
    # applied -- fails closed and is recorded.
    verified = True
    for transformation in transformations:
        span = transformation.span
        if not _in_range(span, length):
            verified = False  # already in `unmaskable`
            continue
        original = text[span.start : span.end]
        if original and original in payload:
            unmaskable.append(span)
            verified = False

    if unmaskable:
        verified = False
    return RedactResult(payload=payload, verified=verified, unmaskable_spans=tuple(unmaskable))


def verify(payload: str, original: str, spans: Sequence[Span]) -> bool:
    """Byte-verify that ``spans`` were removed from ``original`` in ``payload`` (R5.5).

    Returns True iff, for EVERY span, the original bytes
    ``original[span.start:span.end]`` no longer appear anywhere in ``payload``.
    A no-op transformation leaves those bytes present -> False (fail-closed).

    An empty span (``start == end``) carves out no original bytes, so there is
    nothing to remove: it is TRIVIALLY satisfied and never fails ``verify`` by
    itself. Pure. This is the function GW11's dispatch re-verify reuses.
    """
    for span in spans:
        carved = original[span.start : span.end]
        if not carved:
            continue
        if carved in payload:
            return False
    return True
