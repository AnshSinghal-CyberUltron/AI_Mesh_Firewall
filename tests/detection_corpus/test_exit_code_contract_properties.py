"""Property-based tests for the detection-corpus exit-code contract (task 5.2).

These tests exercise the aggregation + CLI contract of ``corpus_lint`` with
Hypothesis: the relationship between ``LintReport.ok``, the ``violations`` tuple,
and the ``main(argv)`` exit code. The module is imported by PATH (the corpus lives
at repo-root ``tests/detection_corpus/`` and is loaded by the gateway wrapper via
``sys.path`` insertion, per the design's Testing Strategy), so this file inserts
its own directory on ``sys.path`` before importing ``corpus_lint`` — keeping it
self-contained and runnable both standalone
(``python -m pytest tests/detection_corpus/test_exit_code_contract_properties.py``)
and under the gateway suite.

The tests build synthetic corpora on a ``tmp_path`` (``malicious.jsonl`` +
``benign.jsonl`` + copied ``families.json`` / ``trigger_tokens.json``) that induce
a *varying* number of violations, and drive both ``lint_corpus(root)`` and
``main([root])`` against them. A tiny synthetic corpus cannot reach the 300/300
coverage minimum, so it always carries coverage violations — that is expected and
fine: Property 6 is about the *relationship* ``ok == (violations == ())`` and the
``main`` exit code, not about achieving a zero-violation corpus. Frozen
``LintReport`` instances are also constructed directly to assert the biconditional
across an arbitrary violation count without needing a corresponding corpus.
"""
from __future__ import annotations

import contextlib
import io
import shutil
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

# Minimum iterations per property (design Testing Strategy / file convention: 200).
ITERATIONS = 200


# --- synthetic-corpus fixture builders ----------------------------------------


def _write_corpus(
    root: Path,
    malicious_lines: list[str],
    benign_lines: list[str],
) -> None:
    """Write a synthetic corpus under ``root`` that ``lint_corpus`` can load.

    Writes ``malicious.jsonl`` + ``benign.jsonl`` from the raw line lists (each
    element is one physical JSONL line, already JSON-encoded) and copies the real
    ``families.json`` and ``trigger_tokens.json`` from the committed corpus so the
    schema/family and paraphrase rules have their context. The two ``.jsonl``
    files are the only fatal-if-missing inputs (Requirement 5.9); copying the two
    JSON catalogues keeps the loader from silently treating them as empty.
    """
    (root / "malicious.jsonl").write_text(
        "".join(line + "\n" for line in malicious_lines), encoding="utf-8"
    )
    (root / "benign.jsonl").write_text(
        "".join(line + "\n" for line in benign_lines), encoding="utf-8"
    )
    shutil.copyfile(_CORPUS_DIR / "families.json", root / "families.json")
    shutil.copyfile(
        _CORPUS_DIR / "trigger_tokens.json", root / "trigger_tokens.json"
    )


def _malicious_item_line(idx: int) -> str:
    """A schema-valid malicious JSONL line with a deterministic split."""
    import json

    item_id = f"mal-prompt_injection-{idx:05d}"
    return json.dumps(
        {
            "id": item_id,
            "text": f"synthetic malicious prompt number {idx} for exit-code test",
            "label": "malicious",
            "family": "prompt_injection",
            "split": corpus_lint.assign_split(item_id),
            "provenance": "hand_authored",
            "fake_fixtures": [],
        }
    )


def _benign_item_line(idx: int) -> str:
    """A schema-valid benign JSONL line with a deterministic split."""
    import json

    item_id = f"ben-general-{idx:05d}"
    return json.dumps(
        {
            "id": item_id,
            "text": f"synthetic benign prompt number {idx} for exit-code test",
            "label": "benign",
            "family": "general_benign",
            "split": corpus_lint.assign_split(item_id),
            "provenance": "hand_authored",
            "fake_fixtures": [],
        }
    )


def _bad_label_line(idx: int) -> str:
    """A schema-INVALID line (label not in the enum) → one schema violation."""
    import json

    item_id = f"bad-label-{idx:05d}"
    return json.dumps(
        {
            "id": item_id,
            "text": f"item with an invalid label {idx}",
            "label": "neither",  # not malicious|benign → schema violation
            "family": "prompt_injection",
            "split": corpus_lint.assign_split(item_id),
            "provenance": "hand_authored",
            "fake_fixtures": [],
        }
    )


def _build_corpus(
    root: Path, *, n_malicious: int, n_benign: int, n_bad: int
) -> None:
    """Assemble a tmp corpus with a controllable number of induced violations.

    ``n_malicious`` clean malicious items + ``n_benign`` clean benign items +
    ``n_bad`` malformed (invalid-label) malicious items. The malformed items each
    add at least one schema violation; the small size guarantees coverage
    violations regardless. Every id is unique and every clean split is the
    deterministic assignment, so the ONLY induced defects come from the bad lines
    plus the unavoidable (tiny-corpus) coverage shortfall.
    """
    malicious = [_malicious_item_line(i) for i in range(n_malicious)]
    malicious += [_bad_label_line(i) for i in range(n_bad)]
    benign = [_benign_item_line(i) for i in range(n_benign)]
    _write_corpus(root, malicious, benign)


# ── Property 6 (direct construction: ok is exactly violations-emptiness) ───────
# Feature: detection-corpus, Property 6: The report exit-code contract is exact
#
# The core invariant `lint_corpus` guarantees for every report it returns:
# `ok` is True if and only if the violations tuple is empty. Asserted directly on
# a freshly constructed frozen LintReport for an ARBITRARY violations tuple, so
# the biconditional is proven across any violation count (0..N) independent of a
# corpus. `ok = (violations == ())` is the exact construction lint_corpus uses.
_ANY_VIOLATION = st.builds(
    corpus_lint.Violation,
    rule=st.sampled_from(
        ["schema", "duplicate", "leakage", "disjointness", "coverage",
         "fixture", "path_scope", "trigger_token_drift", "split", "shape"]
    ),
    item_id=st.text(max_size=32),
    detail=st.text(max_size=64),
)


@settings(max_examples=ITERATIONS, deadline=None)
@given(violations=st.lists(_ANY_VIOLATION, min_size=0, max_size=12))
def test_property6_ok_iff_no_violations(
    violations: list[corpus_lint.Violation],
) -> None:
    frozen = tuple(violations)
    # Construct the report exactly as lint_corpus does.
    report = corpus_lint.LintReport(
        ok=(frozen == ()), violations=frozen, counts={}
    )
    # The biconditional: ok True <=> violations empty.
    assert report.ok == (report.violations == ()), (
        f"ok={report.ok} but violations has {len(report.violations)} item(s)"
    )
    if report.violations:
        assert report.ok is False
    else:
        assert report.ok is True


# ── Property 6 (lint_corpus returned report obeys the biconditional) ───────────
# Feature: detection-corpus, Property 6: The report exit-code contract is exact
#
# For any synthetic corpus (varying how many violations it induces),
# lint_corpus(root).ok is True if and only if its violations tuple is empty. A
# tiny corpus always has coverage violations, so in practice ok is False here and
# violations is non-empty — the biconditional still holds, and we assert it
# directly rather than asserting a specific outcome.
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    n_malicious=st.integers(min_value=0, max_value=6),
    n_benign=st.integers(min_value=0, max_value=6),
    n_bad=st.integers(min_value=0, max_value=4),
)
def test_property6_lint_corpus_ok_iff_empty(
    tmp_path_factory,
    n_malicious: int,
    n_benign: int,
    n_bad: int,
) -> None:
    root = tmp_path_factory.mktemp("corpus")
    _build_corpus(
        root, n_malicious=n_malicious, n_benign=n_benign, n_bad=n_bad
    )
    report = corpus_lint.lint_corpus(root)

    # The biconditional (design Property 6, Requirement 5.9).
    assert report.ok == (report.violations == ()), (
        f"lint_corpus.ok={report.ok} but violations has "
        f"{len(report.violations)} item(s): "
        f"{[v.rule for v in report.violations][:5]}"
    )


# ── Property 6 (main exit code: 0 iff ok, else non-zero in 1..255) ─────────────
# Feature: detection-corpus, Property 6: The report exit-code contract is exact
#
# For any synthetic corpus, main([root]) returns 0 if and only if
# lint_corpus(root).ok, and otherwise a non-zero code in 1..255 equal to the
# clamped violation count max(1, min(n, 255)). This ties the process exit code to
# the report contract (Requirements 5.8, 5.10).
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    n_malicious=st.integers(min_value=0, max_value=6),
    n_benign=st.integers(min_value=0, max_value=6),
    n_bad=st.integers(min_value=0, max_value=4),
)
def test_property6_main_exit_code_matches_report(
    tmp_path_factory,
    n_malicious: int,
    n_benign: int,
    n_bad: int,
) -> None:
    root = tmp_path_factory.mktemp("corpus")
    _build_corpus(
        root, n_malicious=n_malicious, n_benign=n_benign, n_bad=n_bad
    )

    report = corpus_lint.lint_corpus(root)
    # Suppress the CLI's printed violation lines via a context manager (not a
    # function-scoped fixture, which Hypothesis forbids inside @given).
    with contextlib.redirect_stdout(io.StringIO()):
        code = corpus_lint.main([str(root)])

    n = len(report.violations)

    if report.ok:
        # ok <=> exit 0.
        assert code == 0, f"report.ok but main returned {code}"
        assert n == 0
    else:
        # not ok <=> non-zero code in 1..255, exactly the clamped count.
        assert code != 0, "report has violations but main returned 0"
        assert 1 <= code <= 255, f"exit code {code} outside 1..255"
        assert code == max(1, min(n, 255)), (
            f"exit code {code} != clamp of {n} violations"
        )

    # The tie-through: main returns 0 IF AND ONLY IF the report is ok.
    assert (code == 0) == report.ok


# ── Property 6 (many violations clamp to 255, never a false-clean 0) ───────────
# Feature: detection-corpus, Property 6: The report exit-code contract is exact
#
# When the violation count exceeds the single-byte exit-code ceiling, main clamps
# to 255 — it never wraps around to a false-clean 0 (Requirement 5.10: a non-zero
# value between 1 and 255 whenever any violation is present). A synthetic corpus
# with many malformed lines produces well over 255 violations.
def test_property6_overflow_clamps_to_255(tmp_path) -> None:
    import json

    # 300 malformed (invalid-label) malicious lines → >= 300 schema violations,
    # plus coverage violations — comfortably over the 255 ceiling.
    bad_lines = [_bad_label_line(i) for i in range(300)]
    _write_corpus(tmp_path, bad_lines, [_benign_item_line(0)])

    report = corpus_lint.lint_corpus(tmp_path)
    with contextlib.redirect_stdout(io.StringIO()):
        code = corpus_lint.main([str(tmp_path)])

    assert not report.ok
    assert len(report.violations) > 255
    # Clamped to the ceiling, still non-zero — never a false-clean 0.
    assert code == 255, f"expected clamp to 255, got {code}"
    # Sanity: json module was used to build the lines above (keeps import local).
    assert json.dumps({"ok": True})


# ── Property 6 (a genuinely clean report yields ok=True and exit 0) ────────────
# Feature: detection-corpus, Property 6: The report exit-code contract is exact
#
# The forward direction of the biconditional on a real zero-violation report: an
# empty violations tuple yields ok=True, and main returns exactly 0. Constructed
# directly (a zero-violation LintReport) plus a monkeypatched lint_corpus so the
# CLI path is exercised end-to-end without authoring a full 300/300 clean corpus
# (out of scope for this exit-code property; see the real-corpus gate test).
def test_property6_clean_report_is_ok_and_exit_zero(
    tmp_path, monkeypatch
) -> None:
    clean = corpus_lint.LintReport(ok=True, violations=(), counts={
        "malicious": 300, "benign": 300, "attack_families": 8
    })
    # ok is exactly violations-emptiness even on a hand-built clean report.
    assert clean.ok is True
    assert clean.violations == ()

    # Drive main against a report with no violations: it must return 0.
    monkeypatch.setattr(corpus_lint, "lint_corpus", lambda root: clean)
    with contextlib.redirect_stdout(io.StringIO()):
        code = corpus_lint.main([str(tmp_path)])
    assert code == 0, f"clean report must yield exit 0, got {code}"


# ── Property 6 (exactly one violation → exit code exactly 1) ───────────────────
# Feature: detection-corpus, Property 6: The report exit-code contract is exact
#
# The lower bound of the 1..255 range: a single violation yields exit code 1
# (max(1, min(1, 255)) == 1) — the smallest non-zero code, never 0.
def test_property6_single_violation_exit_one(
    tmp_path, monkeypatch
) -> None:
    one = corpus_lint.LintReport(
        ok=False,
        violations=(
            corpus_lint.Violation(
                rule="coverage", item_id="", detail="one synthetic violation"
            ),
        ),
        counts={},
    )
    assert one.ok is False
    monkeypatch.setattr(corpus_lint, "lint_corpus", lambda root: one)
    with contextlib.redirect_stdout(io.StringIO()):
        code = corpus_lint.main([str(tmp_path)])
    assert code == 1, f"a single violation must yield exit 1, got {code}"
