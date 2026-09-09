"""Tests for ``corpus_lint.check_trigger_token_coverage`` and ``check_path_scope``
(task 4.15, Requirements 3.1, 9.1, 9.2).

``check_trigger_token_coverage(tokens)`` re-derives the live literal tokens the
shipped ``ATTACK_PATTERNS`` + ``FUZZY_ANCHOR_PHRASES`` key on (via a lazy,
read-only, ``try/except``-guarded import — Requirement 9) and reports a
``trigger_token_drift`` :class:`Violation` for every live literal the committed
``tokens`` list no longer covers. A scanner-import failure must degrade to a
SINGLE ``trigger_token_drift`` Violation, never a crash (Requirement 9, no blast
radius).

``check_path_scope(root)`` verifies every corpus file resolves under the resolved
corpus root (``tests/detection_corpus/``); a symlink escape yields a
``path_scope`` :class:`Violation` (Requirements 9.1/9.2).

``corpus_lint`` is imported by PATH (the corpus lives at repo-root
``tests/detection_corpus/`` and is loaded via ``sys.path`` insertion, per the
design's Testing Strategy), matching ``test_check_coverage.py`` /
``test_check_fixtures.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# corpus_lint is imported by path: put this file's own directory (the corpus
# root) on sys.path, then import the stdlib-only validator.
_CORPUS_DIR = Path(__file__).resolve().parent
if str(_CORPUS_DIR) not in sys.path:
    sys.path.insert(0, str(_CORPUS_DIR))

import corpus_lint  # noqa: E402  (import must follow the sys.path insertion above)

Violation = corpus_lint.Violation


# ── check_trigger_token_coverage: drift against the derived live literals ──────
#
# The live literal set is whatever ``_derive_live_literals`` extracts from the
# real shipped scanner. These tests are written against that derived set (not a
# hardcoded snapshot) so they stay correct as the scanner evolves: they assert
# the RELATIONSHIP between committed tokens and live literals, which is the
# rule's actual contract (Requirement 3.1).


def _live_literals() -> frozenset[str]:
    literals, error = corpus_lint._derive_live_literals()
    assert error is None, f"scanner import unexpectedly failed: {error}"
    assert literals, "expected the shipped scanner to yield >=1 live literal"
    return literals


def test_empty_committed_list_flags_every_live_literal() -> None:
    # An empty committed trigger-token list covers nothing, so every live literal
    # is uncovered -> one trigger_token_drift Violation per live literal.
    live = _live_literals()
    violations = corpus_lint.check_trigger_token_coverage([])
    assert all(v.rule == "trigger_token_drift" for v in violations)
    assert all(v.item_id == "" for v in violations)
    assert len(violations) == len(live)


def test_committed_list_covering_all_live_literals_is_clean() -> None:
    # A committed list that literally contains every live literal as its own
    # token covers all of them -> zero drift Violations.
    live = _live_literals()
    tokens = [{"token": lit, "source_pattern": "test"} for lit in sorted(live)]
    assert corpus_lint.check_trigger_token_coverage(tokens) == []


def test_removing_one_covered_literal_fires_named_drift() -> None:
    # Cover every live literal except one; that one must be the sole drift.
    live = sorted(_live_literals())
    dropped = live[0]
    tokens = [
        {"token": lit, "source_pattern": "test"}
        for lit in live
        if lit != dropped
    ]
    violations = corpus_lint.check_trigger_token_coverage(tokens)
    # ``dropped`` may still be covered as a substring of a LONGER live literal
    # that remains in the list (the rule treats substring containment as
    # coverage). Only assert a failure when the dropped literal is not a
    # substring of any retained token blob.
    blob = "\n".join(corpus_lint._paraphrase_trigger_tokens(tokens))
    if dropped not in blob:
        assert any(v.rule == "trigger_token_drift" for v in violations)
        assert any(
            dropped in v.detail for v in violations
        ), [v.detail for v in violations]


def test_longer_committed_phrase_covers_substring_literal() -> None:
    # "Covered" means the live literal is a substring of some committed token's
    # normalized text. A committed phrase that CONTAINS a live literal covers it,
    # so enumerating the containing phrases alone is enough to cover their
    # substrings -> zero drift for those literals.
    live = sorted(_live_literals())
    # Wrap each live literal inside a longer phrase; each live literal is then a
    # substring of its wrapper, so every live literal is covered.
    tokens = [
        {"token": f"please {lit} now", "source_pattern": "test"} for lit in live
    ]
    assert corpus_lint.check_trigger_token_coverage(tokens) == []


def test_accepts_plain_string_token_list() -> None:
    # The rule also accepts a plain list[str] of tokens (not only dicts).
    live = sorted(_live_literals())
    tokens = list(live)  # plain strings
    assert corpus_lint.check_trigger_token_coverage(tokens) == []


# ── Requirement 9: scanner-import failure degrades to ONE Violation, no crash ──


def test_scanner_import_failure_yields_single_drift_never_crashes(
    monkeypatch,
) -> None:
    # Force the guarded import inside _derive_live_literals to fail by replacing
    # ``ai_mesh_gateway.scanner`` with a module missing ATTACK_PATTERNS. The rule
    # must NOT raise; it must return exactly one trigger_token_drift Violation
    # describing the import failure (Requirement 9, no blast radius).
    import types

    broken = types.ModuleType("ai_mesh_gateway.scanner")  # no ATTACK_PATTERNS
    monkeypatch.setitem(sys.modules, "ai_mesh_gateway.scanner", broken)

    violations = corpus_lint.check_trigger_token_coverage(
        [{"token": "ignore previous instructions", "source_pattern": "x"}]
    )
    assert len(violations) == 1
    assert violations[0].rule == "trigger_token_drift"
    assert violations[0].item_id == ""
    assert "could not import ATTACK_PATTERNS" in violations[0].detail


def test_derive_live_literals_reports_error_string_on_failure(
    monkeypatch,
) -> None:
    # The helper returns (frozenset(), error_message) — never raises — when the
    # scanner cannot be imported/inspected.
    import types

    broken = types.ModuleType("ai_mesh_gateway.scanner")
    monkeypatch.setitem(sys.modules, "ai_mesh_gateway.scanner", broken)

    literals, error = corpus_lint._derive_live_literals()
    assert literals == frozenset()
    assert isinstance(error, str) and error


# ── check_path_scope: in-scope clean, symlink escape flagged (R9.1/9.2) ────────


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_all_files_in_scope_is_clean(tmp_path: Path) -> None:
    # A corpus root whose files all live under it -> no path_scope violations.
    root = tmp_path / "tests" / "detection_corpus"
    _write(root / "malicious.jsonl", '{"id":"a"}\n')
    _write(root / "benign.jsonl", '{"id":"b"}\n')
    _write(root / "nested" / "families.json", "{}")
    assert corpus_lint.check_path_scope(root) == []


def test_symlink_escape_is_flagged(tmp_path: Path) -> None:
    # A file INSIDE the corpus root that symlinks to a target OUTSIDE the root
    # resolves out of scope -> one path_scope Violation naming the path.
    root = tmp_path / "tests" / "detection_corpus"
    root.mkdir(parents=True)
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("leaked", encoding="utf-8")

    link = root / "escape.jsonl"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - platform w/o symlink
        import pytest

        pytest.skip("symlinks not supported on this platform")

    violations = corpus_lint.check_path_scope(root)
    assert len(violations) == 1
    assert violations[0].rule == "path_scope"
    assert violations[0].item_id == ""
    assert "escape.jsonl" in violations[0].detail or "outside_secret" in violations[0].detail


def test_symlink_within_scope_is_clean(tmp_path: Path) -> None:
    # A symlink whose target is ALSO inside the corpus root stays in scope.
    root = tmp_path / "tests" / "detection_corpus"
    root.mkdir(parents=True)
    real = root / "real.jsonl"
    real.write_text('{"id":"a"}\n', encoding="utf-8")
    link = root / "alias.jsonl"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):  # pragma: no cover
        import pytest

        pytest.skip("symlinks not supported on this platform")

    assert corpus_lint.check_path_scope(root) == []


def test_empty_root_has_no_violations(tmp_path: Path) -> None:
    # An empty (but existing) corpus root has no files -> no path_scope findings.
    root = tmp_path / "tests" / "detection_corpus"
    root.mkdir(parents=True)
    assert corpus_lint.check_path_scope(root) == []
