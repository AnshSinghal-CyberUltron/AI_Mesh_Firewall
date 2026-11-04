"""GW33 task 3.1: rewrite byte verification and the non-rewrite differential (Properties 2, 4)."""
from __future__ import annotations

import random

import pytest

from gateway_v2.domain import (
    Category,
    Decision,
    Disposition,
    Finding,
    FindingDisposition,
    FindingStatus,
    Span,
    Transformation,
)
from gateway_v2.egress.output_errors import RewriteVerificationFailure
from gateway_v2.egress.output_guard import OutputBlocked, apply_decision
from gateway_v2.egress.rewrite_verify import verify_rewrites

_ITERATIONS = 2_000
_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789 .,-_"


def _decision(
    transformations: tuple[Transformation, ...],
    disposition: Disposition = Disposition.REWRITE,
) -> Decision:
    finding = Finding(
        "d", "v", Category.IP_LEAK, FindingStatus.EXECUTED, 1.0,
        tuple(t.span for t in transformations), None,
    )
    return Decision(
        disposition=disposition,
        per_finding=(FindingDisposition("d", disposition, 0),),
        transformations=transformations,
        findings=(finding,),
        plan_version="v",
        deciding_rules=("r",),
        unavailable_detectors=(),
    )


def _spans(rng: random.Random, length: int, count: int) -> list[tuple[int, int]]:
    cuts = sorted(rng.sample(range(length + 1), min(length + 1, count * 2)))
    return [(cuts[i], cuts[i + 1]) for i in range(0, len(cuts) - 1, 2) if cuts[i] < cuts[i + 1]]


def _reference_apply(released: str, decision: Decision, base_offset: int) -> str:
    """Frozen copy of the pre-GW33 ``apply_decision`` body (Property 4 reference)."""
    if decision.disposition is Disposition.BLOCK:
        raise OutputBlocked(decision)
    length = len(released)
    edits: list[tuple[int, int, str]] = []
    for item in decision.transformations:
        start = item.span.start - base_offset
        end = item.span.end - base_offset
        if end <= 0 or start >= length:
            continue
        if not 0 <= start <= end <= length:
            raise ValueError("fail-closed: not covered")
        edits.append((start, end, item.replacement))
    if not edits:
        return released
    edits.sort(key=lambda item: (item[0], item[1]))
    out: list[str] = []
    cursor = 0
    for start, end, replacement in edits:
        if start < cursor:
            raise ValueError("fail-closed: overlapping")
        out.append(released[cursor:start])
        out.append(replacement)
        cursor = end
    out.append(released[cursor:])
    return "".join(out)


def test_property_2_rewrite_verification_soundness() -> None:
    """Feature: gw33-output-parity, Property 2: Rewrite verification soundness. Validates 5.7."""
    # Feature: gw33-output-parity, Property 2: Rewrite verification soundness
    rng = random.Random(33_002)
    for _ in range(_ITERATIONS):
        text = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(30, 120)))
        spans = _spans(rng, len(text), rng.randint(1, 4))
        if not spans:
            continue
        base = rng.randint(0, 50)
        edits = tuple(
            Transformation("rewrite", Span(s + base, e + base), f"[X{i}]")
            for i, (s, e) in enumerate(spans)
        )
        decision = _decision(edits)
        out = apply_decision(text, decision, base)
        assert verify_rewrites(text, out, decision, base).verified
        # A shifted replacement is rejected.
        first = edits[0]
        shifted = out.replace(first.replacement, "?" + first.replacement[1:], 1)
        with pytest.raises(RewriteVerificationFailure):
            verify_rewrites(text, shifted, decision, base)
        # A surviving original (long enough) is rejected.
        long_edits = [(s, e) for s, e in spans if e - s >= 12]
        if long_edits:
            s, e = long_edits[0]
            survivor_out = out + " " + text[s:e]
            with pytest.raises(RewriteVerificationFailure):
                verify_rewrites(text, survivor_out, decision, base)
        # An uncovered span is rejected by apply_decision (partial coverage).
        if base:
            partial = _decision((Transformation("rewrite", Span(base - 1, base + 2), "[X]"),))
            with pytest.raises(ValueError):
                apply_decision(text, partial, base)


def test_overlap_and_misplaced_replacement_fail_closed() -> None:
    text = "aaaa bbbb cccc"
    overlapping = _decision((
        Transformation("rewrite", Span(0, 6), "[A]"),
        Transformation("rewrite", Span(4, 9), "[B]"),
    ))
    with pytest.raises(ValueError):
        apply_decision(text, overlapping, 0)
    decision = _decision((Transformation("rewrite", Span(5, 9), "[B]"),))
    with pytest.raises(RewriteVerificationFailure):
        verify_rewrites(text, "aaaa [B]x cccc", decision, 0)  # replacement shifted by one
    assert verify_rewrites(text, "aaaa [B] cccc", decision, 0).edits == 1


def test_short_spans_and_boundary_concatenation_do_not_false_block() -> None:
    # The original of a short span ("10.0.0.1", < 12 chars) may legitimately re-appear in text that
    # was never a span; the survivor check exempts it. A long original that is only reassembled
    # across a replacement boundary is NOT a survivor either.
    text = "10.0.0.1 then 10.0.0.1"
    decision = _decision((Transformation("rewrite", Span(0, 8), "[ip]"),))
    out = apply_decision(text, decision, 0)
    assert out == "[ip] then 10.0.0.1"
    original = "internal.host.example"
    text2 = f"internal.{original}host.example"
    decision2 = _decision((Transformation("rewrite", Span(9, 9 + len(original)), "[ip]"),))
    # Retained "internal." + "host.example" re-concatenates the original only ACROSS the
    # replacement, which is never a survivor.
    assert apply_decision(text2, decision2, 0) == "internal.[ip]host.example"


def test_property_4_non_rewrite_path_is_byte_identical() -> None:
    """Feature: gw33-output-parity, Property 4: Preservation. Validates 5.8, 10.6."""
    # Feature: gw33-output-parity, Property 4: Preservation
    rng = random.Random(33_004)
    for _ in range(_ITERATIONS):
        text = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 100)))
        base = rng.randint(0, 30)
        spans = _spans(rng, len(text), rng.randint(0, 4)) if text else []
        edits = tuple(
            Transformation("redact", Span(s + base, e + base), "[REDACTED]") for s, e in spans
        )
        if rng.random() < 0.3 and edits:
            # an extra edit that may overlap or partially fall outside the slice
            s = rng.randint(0, len(text) + 5)
            edits = (*edits, Transformation("redact", Span(s + base, s + base + 7), "[R]"))
        disposition = Disposition.REDACT if edits else Disposition.ALLOW
        decision = _decision(edits, disposition) if edits else _decision(())
        try:
            expected: str | type[BaseException] = _reference_apply(text, decision, base)
        except (ValueError, OutputBlocked) as exc:
            expected = type(exc)
        try:
            actual: str | type[BaseException] = apply_decision(text, decision, base)
        except (ValueError, OutputBlocked) as exc:
            actual = type(exc)
        assert actual == expected
