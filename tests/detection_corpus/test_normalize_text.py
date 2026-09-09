"""Unit tests for ``corpus_lint.normalize_text`` (task 3.5).

``normalize_text`` is the documented Normalized_Text transformation
(Requirement 3.2): lowercase -> NFKC -> collapse consecutive whitespace to a
single ASCII space -> strip. These tests are self-contained: they insert the
corpus directory on ``sys.path`` and import ``corpus_lint`` directly so the file
runs standalone via ``pytest tests/detection_corpus/test_normalize_text.py``.

Requirements: 3.2
"""

from __future__ import annotations

import sys
from pathlib import Path

# Keep the test self-contained: import corpus_lint from this directory without
# depending on any package rootedness / conftest.
_CORPUS_DIR = Path(__file__).resolve().parent
if str(_CORPUS_DIR) not in sys.path:
    sys.path.insert(0, str(_CORPUS_DIR))

import corpus_lint  # noqa: E402  (import after sys.path insertion)

normalize_text = corpus_lint.normalize_text


# --- case-folding --------------------------------------------------------------


def test_lowercases_ascii():
    assert normalize_text("HELLO World") == "hello world"


def test_mixed_case_is_folded():
    assert normalize_text("IgNoRe PrEvIoUs") == "ignore previous"


def test_already_lowercase_is_unchanged():
    assert normalize_text("ignore previous instructions") == (
        "ignore previous instructions"
    )


def test_non_ascii_case_folding():
    # Latin-1 accented uppercase folds to its lowercase form.
    assert normalize_text("ÀÉÎ") == "àéî"


# --- NFKC folding --------------------------------------------------------------


def test_fullwidth_letters_fold_to_ascii():
    # Fullwidth "ｉｇｎｏｒｅ" (U+FF49..) -> NFKC -> "ignore".
    assert normalize_text("\uff49\uff47\uff4e\uff4f\uff52\uff45") == "ignore"


def test_fullwidth_mixed_case_folds_and_lowercases():
    # Fullwidth uppercase "ＩＧＮＯＲＥ" -> lowercase ascii "ignore".
    assert normalize_text("\uff29\uff27\uff2e\uff2f\uff32\uff25") == "ignore"


def test_ligature_fi_expands():
    # "ﬁ" (U+FB01 LATIN SMALL LIGATURE FI) -> NFKC -> "fi".
    assert normalize_text("\ufb01le") == "file"


def test_ligature_fl_expands():
    # "ﬂ" (U+FB02 LATIN SMALL LIGATURE FL) -> NFKC -> "fl".
    assert normalize_text("con\ufb02ict") == "conflict"


def test_fullwidth_digits_fold_to_ascii():
    # Fullwidth digits "７８９" -> "789".
    assert normalize_text("\uff17\uff18\uff19") == "789"


def test_circled_number_folds():
    # "①" (U+2460 CIRCLED DIGIT ONE) -> NFKC -> "1".
    assert normalize_text("\u2460") == "1"


# --- whitespace collapse -------------------------------------------------------


def test_multiple_spaces_collapse_to_one():
    assert normalize_text("a     b") == "a b"


def test_tabs_collapse_to_single_space():
    assert normalize_text("a\t\tb") == "a b"


def test_newlines_collapse_to_single_space():
    assert normalize_text("a\n\nb") == "a b"


def test_carriage_return_collapses():
    assert normalize_text("a\r\nb") == "a b"


def test_mixed_whitespace_run_collapses_to_one_space():
    assert normalize_text("a \t\n  \r b") == "a b"


def test_nbsp_is_collapsed_and_normalized():
    # NBSP (U+00A0): NFKC folds it to a regular space and it is whitespace, so a
    # run around/of it collapses to a single ASCII space.
    assert normalize_text("a\u00a0b") == "a b"


def test_nbsp_run_between_words_collapses():
    assert normalize_text("a\u00a0\u00a0\u00a0b") == "a b"


def test_internal_single_space_preserved():
    assert normalize_text("hello world") == "hello world"


# --- strip edge cases ----------------------------------------------------------


def test_leading_whitespace_stripped():
    assert normalize_text("   hello") == "hello"


def test_trailing_whitespace_stripped():
    assert normalize_text("hello   ") == "hello"


def test_leading_and_trailing_whitespace_stripped():
    assert normalize_text("  \t hello world \n ") == "hello world"


def test_leading_trailing_nbsp_stripped():
    assert normalize_text("\u00a0hello\u00a0") == "hello"


def test_empty_string_returns_empty():
    assert normalize_text("") == ""


def test_whitespace_only_returns_empty():
    assert normalize_text("   ") == ""


def test_mixed_whitespace_only_returns_empty():
    assert normalize_text(" \t\n\r \u00a0 ") == ""


# --- combined / order-of-operations -------------------------------------------


def test_all_transforms_together():
    # Fullwidth uppercase + ligature + tabs/newlines + surrounding whitespace.
    raw = "  \t\uff29\uff27\uff2e\uff2f\uff32\uff25\n\n the \ufb01le \r "
    assert normalize_text(raw) == "ignore the file"


def test_pure_function_no_side_effects():
    # Calling twice on the same input yields the same result (determinism).
    raw = "  HeLLo   \tWorld\n"
    first = normalize_text(raw)
    second = normalize_text(raw)
    assert first == second == "hello world"
    # Input object is untouched (strings are immutable, but assert the contract).
    assert raw == "  HeLLo   \tWorld\n"
