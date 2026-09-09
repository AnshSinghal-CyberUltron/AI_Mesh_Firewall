"""Tests for ``corpus_lint.check_fixtures`` (task 4.13, Requirements 10.1/10.4/10.5).

``check_fixtures(items)`` scans each Corpus_Item's ``text`` for conservative
PII/secret shapes (SSN, credit-card-like run, email, API-key-like token). A
detected value that is NOT listed in the item's ``fake_fixtures`` marker list is
an undocumented sensitive-looking value and must yield one ``rule="fixture"``
:class:`Violation`; a value present in ``fake_fixtures`` (the "documented marker"
of Requirement 10.5) is a Fake_Fixture and must produce no violation.

``corpus_lint`` is imported by PATH (the corpus lives at repo-root
``tests/detection_corpus/`` and is loaded via ``sys.path`` insertion, per the
design's Testing Strategy), so this file inserts its own directory on
``sys.path`` before importing the stdlib-only validator — matching
``test_corpus_lint_properties.py`` and keeping the file runnable both standalone
and under the gateway suite.
"""
from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

# corpus_lint is imported by path (see module docstring): put this file's own
# directory (the corpus root) on sys.path, then import the stdlib-only validator.
_CORPUS_DIR = Path(__file__).resolve().parent
if str(_CORPUS_DIR) not in sys.path:
    sys.path.insert(0, str(_CORPUS_DIR))

import corpus_lint  # noqa: E402  (import must follow the sys.path insertion above)

# Minimum iterations per property (design Testing Strategy: >= 100).
ITERATIONS = 200


def _item(text: str, *, item_id: str = "item-1", fake_fixtures=None) -> corpus_lint.LoadedItem:
    """Build a well-formed :class:`LoadedItem` carrying ``text`` (and markers).

    Only the fields ``check_fixtures`` reads (``id``, ``text``, ``fake_fixtures``)
    are populated; the item is otherwise the minimal usable shape.
    """
    obj: dict = {"id": item_id, "text": text}
    if fake_fixtures is not None:
        obj["fake_fixtures"] = fake_fixtures
    return corpus_lint.LoadedItem(line=1, source="malicious.jsonl", obj=obj)


# ── Detection: each PII/secret shape ──────────────────────────────────────────


def test_ssn_shape_unmarked_is_violation() -> None:
    items = [_item("my number is 123-45-6789 ok")]
    violations = corpus_lint.check_fixtures(items)
    assert len(violations) == 1
    assert violations[0].rule == "fixture"
    assert violations[0].item_id == "item-1"
    assert "ssn" in violations[0].detail
    assert "123-45-6789" in violations[0].detail


def test_credit_card_shape_unmarked_is_violation() -> None:
    items = [_item("card 4111 1111 1111 1111 here")]
    violations = corpus_lint.check_fixtures(items)
    assert [v.rule for v in violations] == ["fixture"]
    assert "credit_card" in violations[0].detail


def test_email_shape_unmarked_is_violation() -> None:
    items = [_item("reach me at alice@example.com please")]
    violations = corpus_lint.check_fixtures(items)
    assert [v.rule for v in violations] == ["fixture"]
    assert "email" in violations[0].detail
    assert "alice@example.com" in violations[0].detail


def test_api_key_shape_unmarked_is_violation() -> None:
    items = [_item("token sk-ABCDEFGHIJKLMNOPqrstuvwx used")]
    violations = corpus_lint.check_fixtures(items)
    assert [v.rule for v in violations] == ["fixture"]
    assert "api_key" in violations[0].detail


# ── Documented marker (Requirement 10.5) ──────────────────────────────────────


def test_marked_value_produces_no_violation() -> None:
    items = [
        _item(
            "my number is 123-45-6789 ok",
            fake_fixtures=["123-45-6789"],
        )
    ]
    assert corpus_lint.check_fixtures(items) == []


def test_marker_comparison_is_normalized() -> None:
    # Marker differs only by case/whitespace from the detected value; normalized
    # comparison must still treat it as documented.
    items = [
        _item(
            "reach me at Alice@Example.com please",
            fake_fixtures=["  alice@example.com  "],
        )
    ]
    assert corpus_lint.check_fixtures(items) == []


def test_partial_marker_still_flags_unmarked_value() -> None:
    # One sensitive value is marked, a second (different) one is not: only the
    # unmarked value is a violation.
    items = [
        _item(
            "ssn 123-45-6789 and email bob@example.org",
            fake_fixtures=["123-45-6789"],
        )
    ]
    violations = corpus_lint.check_fixtures(items)
    assert [v.rule for v in violations] == ["fixture"]
    assert "email" in violations[0].detail
    assert "bob@example.org" in violations[0].detail


# ── De-duplication and clean text ─────────────────────────────────────────────


def test_repeated_value_in_one_item_reported_once() -> None:
    items = [_item("123-45-6789 then again 123-45-6789")]
    violations = corpus_lint.check_fixtures(items)
    assert len(violations) == 1


def test_clean_prose_produces_no_violation() -> None:
    items = [_item("This is an ordinary sentence with no sensitive values.")]
    assert corpus_lint.check_fixtures(items) == []


# ── Unusable items are skipped (Requirement 1.8) ──────────────────────────────


def test_parse_failed_item_is_skipped() -> None:
    bad = corpus_lint.LoadedItem(
        line=3, source="malicious.jsonl", obj=None, parse_error="boom"
    )
    assert corpus_lint.check_fixtures([bad]) == []


def test_item_without_usable_id_or_text_is_skipped() -> None:
    no_id = corpus_lint.LoadedItem(
        line=1, source="s.jsonl", obj={"text": "123-45-6789"}
    )
    no_text = corpus_lint.LoadedItem(
        line=2, source="s.jsonl", obj={"id": "x", "text": 123}
    )
    assert corpus_lint.check_fixtures([no_id, no_text]) == []


# ── Property: a marked value is never a fixture violation (Requirement 10.5) ──
# Feature: detection-corpus, check_fixtures — every detected sensitive value that
# is listed verbatim in fake_fixtures is a documented Fake_Fixture, so wrapping any
# text in an item whose fake_fixtures contains every SSN present yields no fixture
# violation for that SSN shape.
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    prefix=st.text(alphabet=st.characters(blacklist_characters="0123456789-@"), max_size=20),
    a=st.integers(min_value=0, max_value=999),
    b=st.integers(min_value=0, max_value=99),
    c=st.integers(min_value=0, max_value=9999),
)
def test_property_marked_ssn_never_violates(prefix: str, a: int, b: int, c: int) -> None:
    ssn = f"{a:03d}-{b:02d}-{c:04d}"
    text = f"{prefix} {ssn} tail"
    marked = _item(text, fake_fixtures=[ssn])
    # No violation names this SSN value when it is documented as a fake fixture.
    for v in corpus_lint.check_fixtures([marked]):
        assert ssn not in v.detail, (
            f"marked SSN {ssn!r} produced a fixture violation: {v.detail!r}"
        )
