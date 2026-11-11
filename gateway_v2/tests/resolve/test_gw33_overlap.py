"""GW33 task 5.5 / Property 15: edit normalisation (D-7)."""
from __future__ import annotations

import random

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
from gateway_v2.egress.output_guard import apply_decision
from gateway_v2.resolve.rewrite import normalise_edits, render_template

_PLACEHOLDER = "[REDACTED]"
_ITERATIONS = 5_000


def _decision(edits: tuple[Transformation, ...]) -> Decision:
    finding = Finding("d", "v", Category.EXFIL, FindingStatus.EXECUTED, 1.0, (), None)
    return Decision(
        Disposition.REDACT, (FindingDisposition("d", Disposition.REDACT, 0),), edits,
        (finding,), "v", ("r",), (),
    )


def _union(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def test_property_15_normalised_edits_are_disjoint_cover_the_union_and_apply() -> None:
    """Feature: gw33-output-parity, Property 15: Edit normalisation. Validates 5.2, 5.7."""
    # Feature: gw33-output-parity, Property 15: Edit normalisation
    rng = random.Random(33_015)
    kinds = (("redact", _PLACEHOLDER), ("rewrite", "[ip]"), ("rewrite", "[url]"))
    for _ in range(_ITERATIONS):
        length = rng.randint(20, 80)
        edits = []
        for _n in range(rng.randint(1, 6)):
            start = rng.randint(0, length - 2)
            end = rng.randint(start + 1, min(length, start + 15))
            kind, repl = rng.choice(kinds)
            edits.append(Transformation(kind, Span(start, end), repl))
        out, codes = normalise_edits(edits, _PLACEHOLDER)
        spans = sorted((t.span.start, t.span.end) for t in out)
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))
        assert spans == _union([(t.span.start, t.span.end) for t in edits])
        # A component keeps its kind only when every member agreed on kind and replacement.
        collapsed = {(t.kind, t.replacement) for t in out}
        if "edit_overlap_redacted" not in codes:
            assert collapsed <= {(t.kind, t.replacement) for t in edits}
        text = "".join(rng.choice("abcdefgh") for _ in range(length))
        apply_decision(text, _decision(out), 0)  # never raises on normalised output


def test_identical_kind_and_replacement_keep_their_kind() -> None:
    edits = (
        Transformation("rewrite", Span(0, 6), "[ip]"),
        Transformation("rewrite", Span(4, 10), "[ip]"),
    )
    out, codes = normalise_edits(edits, _PLACEHOLDER)
    assert out == (Transformation("rewrite", Span(0, 10), "[ip]"),)
    assert codes == ()


def test_rewrite_inside_a_redact_and_different_templates_become_one_redact() -> None:
    edits = (
        Transformation("rewrite", Span(5, 15), "[url]"),  # exfil URL
        Transformation("rewrite", Span(8, 12), "[ip]"),   # RFC1918 address inside it
    )
    out, codes = normalise_edits(edits, _PLACEHOLDER)
    assert out == (Transformation("redact", Span(5, 15), _PLACEHOLDER),)
    assert codes == ("edit_overlap_redacted",)
    chain = (
        Transformation("redact", Span(0, 10), _PLACEHOLDER),
        Transformation("rewrite", Span(8, 16), "[x]"),
        Transformation("redact", Span(14, 20), _PLACEHOLDER),
    )
    out, codes = normalise_edits(chain, _PLACEHOLDER)
    assert out == (Transformation("redact", Span(0, 20), _PLACEHOLDER),)


def test_touching_edits_never_merge_and_disjoint_input_order_is_kept() -> None:
    edits = (
        Transformation("rewrite", Span(6, 9), "[b]"),
        Transformation("redact", Span(0, 3), _PLACEHOLDER),
        Transformation("rewrite", Span(3, 6), "[a]"),
    )
    out, codes = normalise_edits(edits, _PLACEHOLDER)
    assert out == edits and codes == ()


def test_render_template_substitutes_exactly_two_tokens() -> None:
    assert render_template("[{category}/{rule_id}]", Category.IP_LEAK, "r1") == "[ip_leak/r1]"
    assert render_template("{other}", Category.PII, "r") == "{other}"
