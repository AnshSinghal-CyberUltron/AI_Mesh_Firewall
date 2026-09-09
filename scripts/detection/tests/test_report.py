"""Property + example tests for ``report.py`` (posture scoring harness).

Additive, measurement-only (Requirement 9): this file lives under
``scripts/detection/`` and touches no production path.

Task 8.2 implements Property 14 (report family-completeness): a scored posture
presents, for every present attack family, a per-family recall cell (a float in
0.0..1.0 or an explicit NotComputable marker) alongside that family's malicious
count, and for every present benign family a per-family FPR cell (a value or
NotComputable) alongside that family's benign count — no cell for a present
family is left empty or missing.
"""

from __future__ import annotations

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# The detection modules import as top-level names with ``scripts/detection`` on
# the path (they are a self-contained additive package). Make that import work
# whether pytest is invoked from the repo root, the gateway dir, or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import attribution  # noqa: E402
import metrics  # noqa: E402
import report  # noqa: E402


# ---------------------------------------------------------------------------
# Property 14 — report family-completeness
# ---------------------------------------------------------------------------
# Uniquely-named strategies/helpers (``_famcomplete_`` prefix) so this file can
# be appended to concurrently by sibling tasks (e.g. task 8.3) without symbol
# collisions.

# Family-name generator: non-empty short identifiers, kept distinct within a set
# by drawing into a ``st.lists(..., unique=True)``. ``paraphrase`` and
# ``developer_traffic`` (the distinguished families) are always eligible so the
# property also exercises them.
_famcomplete_family_names = st.one_of(
    st.sampled_from(["paraphrase", "developer_traffic"]),
    st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122),
        min_size=1,
        max_size=12,
    ),
)

# A per-family cell is EITHER a float in the metric range [0.0, 1.0] OR a
# ``metrics.NotComputable`` sentinel — exactly the two shapes a real scored
# posture can carry (Requirements 4.5, 5.5, 10.1). Draw both.
_famcomplete_cell = st.one_of(
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.builds(metrics.NotComputable, reason=st.text(min_size=1, max_size=24)),
)


@st.composite
def _famcomplete_scored_posture(draw):
    """Draw a *scored* ``PostureResult`` over present attack + benign families.

    Builds ``per_family_recall`` over a set of present attack families, each cell
    a float-in-range or a ``NotComputable``; ``per_family_fpr`` likewise over a
    set of present benign families; and ``family_counts`` carrying a non-negative
    count for every present family in either map. This mirrors what a scored
    posture carries at the report boundary.
    """
    attack_families = draw(
        st.lists(_famcomplete_family_names, min_size=1, max_size=6, unique=True)
    )
    benign_families = draw(
        st.lists(_famcomplete_family_names, min_size=1, max_size=6, unique=True)
    )

    per_family_recall = {fam: draw(_famcomplete_cell) for fam in attack_families}
    per_family_fpr = {fam: draw(_famcomplete_cell) for fam in benign_families}

    # A count backs every present family in either map (Requirements 4.4, 5.4).
    present_families = set(attack_families) | set(benign_families)
    family_counts = {
        fam: draw(st.integers(min_value=0, max_value=10_000))
        for fam in present_families
    }

    # A scored posture must carry a headline value; supply an achievable-target
    # form so ``PostureResult.__post_init__`` accepts it as ``scored``.
    return report.PostureResult(
        posture_name=draw(
            st.text(
                alphabet=st.characters(min_codepoint=97, max_codepoint=122),
                min_size=1,
                max_size=16,
            )
        ),
        status=report.STATUS_SCORED,
        recall_at_target_fpr=draw(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
        ),
        selected_threshold=draw(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
        ),
        achieved_fpr=draw(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
        ),
        target_fpr_achievable=True,
        per_family_recall=per_family_recall,
        per_family_fpr=per_family_fpr,
        family_counts=family_counts,
    )


def _famcomplete_is_valid_cell(cell) -> bool:
    """A present-family cell is a float in [0.0, 1.0] OR a NotComputable marker.

    It must never be ``None`` and must never be missing (the caller checks
    membership separately). ``bool`` is rejected explicitly so a stray boolean is
    not mistaken for a float value.
    """
    if isinstance(cell, metrics.NotComputable):
        return bool(cell.reason)  # the marker must be explicit / non-empty
    if isinstance(cell, bool):
        return False
    if isinstance(cell, float):
        return 0.0 <= cell <= 1.0
    return False


@settings(max_examples=200)
@given(result=_famcomplete_scored_posture())
def test_report_family_completeness_property(result):
    """Feature: posture-scoring, Property 14: For any scored posture over a
    scored set, the report presents, for every attack family present, a
    per-family recall cell (a value in 0.0 to 1.0 or an explicit "not computable"
    marker) alongside that family's malicious count; and for every benign family
    present, a per-family FPR cell (a value or "not computable") alongside that
    family's benign count — no cell for a present family is left empty or missing.

    Validates: Requirements 4.3, 4.4, 5.3, 5.4, 10.1
    """
    # Only a scored posture presents metric cells at all (Requirement 8.2).
    assert result.is_scored

    # Every PRESENT attack family: a recall cell that is a value-in-range or an
    # explicit not-computable marker (never None / missing / empty), AND a
    # family_counts entry backing it (Requirements 4.3, 4.4, 10.1).
    for family, cell in result.per_family_recall.items():
        assert cell is not None, f"attack family {family!r} recall cell is None"
        assert _famcomplete_is_valid_cell(cell), (
            f"attack family {family!r} recall cell {cell!r} is neither a "
            "float in [0.0, 1.0] nor a NotComputable marker"
        )
        assert family in result.family_counts, (
            f"attack family {family!r} present in per_family_recall but has no "
            "family_counts entry"
        )
        assert result.family_counts[family] >= 0

    # Every PRESENT benign family: an FPR cell that is a value-in-range or an
    # explicit not-computable marker, AND a family_counts entry backing it
    # (Requirements 5.3, 5.4, 10.1).
    for family, cell in result.per_family_fpr.items():
        assert cell is not None, f"benign family {family!r} fpr cell is None"
        assert _famcomplete_is_valid_cell(cell), (
            f"benign family {family!r} fpr cell {cell!r} is neither a float in "
            "[0.0, 1.0] nor a NotComputable marker"
        )
        assert family in result.family_counts, (
            f"benign family {family!r} present in per_family_fpr but has no "
            "family_counts entry"
        )
        assert result.family_counts[family] >= 0

    # No present family cell is left missing: the count map covers every present
    # family in either metric map (no orphan present family without a count).
    present_families = set(result.per_family_recall) | set(result.per_family_fpr)
    assert present_families.issubset(set(result.family_counts)), (
        "some present family has no family_counts entry: "
        f"{present_families - set(result.family_counts)!r}"
    )


@settings(max_examples=100)
@given(result=_famcomplete_scored_posture())
def test_report_family_completeness_survives_build_report(result):
    """Feature: posture-scoring, Property 14 (through ``build_report``): a scored
    posture's per-family recall/FPR cells and family counts are carried through
    the report assembly unchanged, so completeness holds on the assembled
    ``PostureReport`` entry, not only on the raw result.

    Validates: Requirements 4.3, 4.4, 5.3, 5.4, 10.1
    """
    # ``build_report`` requires exactly three postures; pad with two not-scored
    # siblings so the scored posture under test flows through unchanged.
    others = (
        report.PostureResult(
            posture_name="_famcomplete_other_a",
            status=report.STATUS_NOT_SCORED,
            not_scored_reason="not exercised by this property",
        ),
        report.PostureResult(
            posture_name="_famcomplete_other_b",
            status=report.STATUS_NOT_SCORED,
            not_scored_reason="not exercised by this property",
        ),
    )
    scored_set_meta = report.ScoredSetMeta(
        which="eval",
        malicious_count=0,
        benign_count=0,
    )
    attrib = attribution.Attribution(
        corpus_version="deadbeef",
        reproducible_command=attribution.reproducible_command(),
        target_fpr=attribution.target_fpr(),
    )

    built = report.build_report((result, *others), scored_set_meta, attrib)

    # Locate the scored posture in the assembled report and re-assert Property 14.
    entry = next(p for p in built.postures if p.posture_name == result.posture_name)
    assert entry.is_scored

    for family, cell in entry.per_family_recall.items():
        assert _famcomplete_is_valid_cell(cell)
        assert family in entry.family_counts
    for family, cell in entry.per_family_fpr.items():
        assert _famcomplete_is_valid_cell(cell)
        assert family in entry.family_counts

    present_families = set(entry.per_family_recall) | set(entry.per_family_fpr)
    assert present_families.issubset(set(entry.family_counts))

# ---------------------------------------------------------------------------
# Task 8.5 — report content + emission (plain pytest unit tests)
# ---------------------------------------------------------------------------
# Uniquely-named helpers/functions (``_content_`` / ``test_report_content_`` /
# ``test_emit_`` prefixes) so this block coexists with the Property 14/15 tests
# above without symbol collision. These cover:
#   * render() states the threshold rule incl. "tuned on train split only" (3.8)
#   * render() states the Target_FPR 0.01 (6.6)
#   * render() states the scored set + malicious/benign + excluded counts (6.4)
#   * render() states the corpus version (6.5)
#   * render() states the reproducible command (6.6)
#   * no foreign constant: every rendered metric traces to a report field (7.8)
#   * emit() writes exactly one report file whose bytes == render() (6.1)
#   * a forced write failure raises EmitError, leaves no partial/temp file (6.8)

import pytest  # noqa: E402


def _content_attribution() -> "attribution.Attribution":
    """A resolved Attribution with a distinctive corpus version for content asserts."""
    return attribution.Attribution(
        corpus_version="c0ffee1234567890",
        reproducible_command=attribution.reproducible_command(),
        target_fpr=attribution.target_fpr(),
    )


def _content_full_report() -> "report.PostureReport":
    """Build a full three-posture ``PostureReport`` mixing scored + not-scored.

    The scored posture carries deliberately-chosen metric values (recall 0.7654,
    threshold 0.4321, achieved FPR 0.0099, one paraphrase family + one benign
    family) so the content tests can assert those exact rendered numbers AND that
    a value never placed on the report does NOT appear (the 7.8 no-foreign-constant
    check). A second posture is scored via the FPR-floor form; a third is not
    scored (carries only its reason).
    """
    scored = report.PostureResult(
        posture_name="tier1_only",
        status=report.STATUS_SCORED,
        recall_at_target_fpr=0.7654,
        selected_threshold=0.4321,
        achieved_fpr=0.0099,
        target_fpr_achievable=True,
        per_family_recall={
            "paraphrase": 0.5000,
            "prompt_injection": metrics.NotComputable(reason="0 malicious items"),
        },
        per_family_fpr={"developer_traffic": 0.0100},
        family_counts={"paraphrase": 12, "prompt_injection": 0, "developer_traffic": 34},
        paraphrase_gap=0.2500,
    )
    floor = report.PostureResult(
        posture_name="tier1_plus_policy",
        status=report.STATUS_SCORED,
        target_fpr_achievable=False,
        fpr_floor=0.0500,
        recall_at_floor=0.9000,
        selected_threshold=0.1234,
        achieved_fpr=0.0500,
        per_family_recall={"paraphrase": 0.8000},
        per_family_fpr={"developer_traffic": 0.0500},
        family_counts={"paraphrase": 5, "developer_traffic": 7},
    )
    not_scored = report.PostureResult(
        posture_name="tier1_plus_semantic",
        status=report.STATUS_NOT_SCORED,
        not_scored_reason="ENABLE_TIER2 is off; Bedrock unreachable",
    )
    scored_set_meta = report.ScoredSetMeta(
        which="eval",
        malicious_count=111,
        benign_count=222,
        excluded_label_count=3,
        excluded_split_count=4,
    )
    return report.build_report(
        (scored, floor, not_scored), scored_set_meta, _content_attribution()
    )


def test_report_content_states_threshold_rule_tuned_on_train_split_only():
    """render() states the threshold-selection rule incl. "tuned on train split
    only" and the Target_FPR 0.01 (Requirements 3.8, 6.6)."""
    rep = _content_full_report()
    rendered = report.render(rep)

    # The full threshold rule text and the mandatory phrase both appear (3.8).
    assert report.THRESHOLD_RULE_TEXT in rendered
    assert "tuned on train split only" in rendered

    # The Target_FPR 0.01 is stated (6.6). It traces to the report field.
    assert str(rep.target_fpr) in rendered
    assert rep.target_fpr == 0.01


def test_report_content_states_scored_set_and_counts():
    """render() states the scored set (eval/full) with malicious/benign counts and
    the excluded counts (Requirement 6.4, plus 2.7/7.7 excluded reconciliation)."""
    rep = _content_full_report()
    rendered = report.render(rep)
    meta = rep.scored_set

    assert meta.which in rendered  # "eval"
    assert str(meta.malicious_count) in rendered  # 111
    assert str(meta.benign_count) in rendered  # 222
    assert str(meta.excluded_label_count) in rendered  # 3
    assert str(meta.excluded_split_count) in rendered  # 4


def test_report_content_states_corpus_version():
    """render() states the Corpus_Version identifying the corpus contents (6.5)."""
    rep = _content_full_report()
    rendered = report.render(rep)
    assert rep.corpus_version in rendered
    assert rep.corpus_version == "c0ffee1234567890"


def test_report_content_states_reproducible_command():
    """render() states the Reproducible_Command (Requirement 6.6)."""
    rep = _content_full_report()
    rendered = report.render(rep)
    assert rep.reproducible_command in rendered
    # It is the single documented gateway-venv command.
    assert "score_postures.py" in rep.reproducible_command


def test_report_content_scored_metric_values_appear_formatted():
    """Every scored metric value the report carries appears in the render, 4 dp
    formatted — the positive half of the no-foreign-constant check (7.8)."""
    rep = _content_full_report()
    rendered = report.render(rep)

    # The scored posture's headline + threshold + achieved FPR + paraphrase gap,
    # each traced to its field and 4-dp formatted.
    assert "0.7654" in rendered  # recall_at_target_fpr
    assert "0.4321" in rendered  # selected_threshold
    assert "0.0099" in rendered  # achieved_fpr
    assert "0.2500" in rendered  # paraphrase_gap
    assert "0.5000" in rendered  # per-family recall (paraphrase)
    assert "0.0100" in rendered  # per-family fpr (developer_traffic)

    # The FPR-floor posture's floor form + counts.
    assert "0.0500" in rendered  # fpr_floor / achieved_fpr / per-family fpr
    assert "0.9000" in rendered  # recall_at_floor
    assert "0.1234" in rendered  # selected_threshold

    # The not-scored posture states its reason and no metric value form.
    assert "ENABLE_TIER2 is off; Bedrock unreachable" in rendered
    assert "not scored" in rendered


def test_report_content_no_foreign_constant_appears():
    """No obviously-foreign metric constant appears — a 4-dp value that was NEVER
    placed on any report field must not surface in the render (Requirement 7.8).

    This is the negative half of 7.8: every rendered number must trace to a
    computed field. We pick values in the metric's 4-dp shape that are NOT any of
    the fields on the report and assert their absence."""
    rep = _content_full_report()
    rendered = report.render(rep)

    # Distinctive 4-dp values not assigned to any field on this report.
    for foreign in ("0.6789", "0.3333", "0.8888", "0.9999", "0.1357"):
        assert foreign not in rendered, (
            f"foreign constant {foreign!r} appeared in the report but traces to "
            "no computed field (Requirement 7.8)"
        )


def test_report_content_notcomputable_family_cell_rendered_explicit():
    """A present family with 0 items renders the explicit not-computable marker,
    never a blank/fabricated number (Requirements 4.5, 5.5, 10.1, 7.8)."""
    rep = _content_full_report()
    rendered = report.render(rep)
    # prompt_injection has 0 malicious items -> NotComputable cell -> marker.
    assert report.NOT_COMPUTABLE_MARKER in rendered


def test_emit_writes_exactly_one_report_file_with_render_bytes(tmp_path):
    """emit() writes exactly one report file under a docs/perf-like dir, and its
    content is byte-identical to render(report) (Requirement 6.1)."""
    rep = _content_full_report()
    perf_dir = tmp_path / "docs" / "perf"
    out_path = perf_dir / "posture_scores.md"

    report.emit(rep, out_path)

    # Exactly one file exists in the directory, and it is the report.
    files = list(perf_dir.iterdir())
    assert files == [out_path], f"expected exactly one report file, found {files!r}"
    assert out_path.is_file()

    # Its content is byte-for-byte the render of the report (no fabrication).
    assert out_path.read_text(encoding="utf-8") == report.render(rep)


def test_emit_creates_parent_perf_directory(tmp_path):
    """emit() creates the docs/perf parent directory when absent (6.1)."""
    rep = _content_full_report()
    out_path = tmp_path / "docs" / "perf" / "posture_scores.md"
    assert not out_path.parent.exists()

    report.emit(rep, out_path)

    assert out_path.parent.is_dir()
    assert out_path.is_file()


def test_emit_write_failure_raises_and_leaves_no_partial_file(tmp_path, monkeypatch):
    """A forced write failure raises EmitError and leaves NO partial report file
    at out_path AND NO leftover temp file in the directory (Requirement 6.8)."""
    rep = _content_full_report()
    perf_dir = tmp_path / "docs" / "perf"
    perf_dir.mkdir(parents=True)
    out_path = perf_dir / "posture_scores.md"

    # Force the atomic publish to fail after the temp file has been written.
    def _boom(src, dst):
        raise OSError("simulated os.replace failure")

    monkeypatch.setattr(report.os, "replace", _boom)

    with pytest.raises(report.EmitError):
        report.emit(rep, out_path)

    # No partial/leftover report at the final path.
    assert not out_path.exists()
    # No leftover temp file in the destination directory.
    leftovers = list(perf_dir.iterdir())
    assert leftovers == [], f"expected no leftover files, found {leftovers!r}"


def test_emit_mkstemp_failure_raises_and_leaves_no_file(tmp_path, monkeypatch):
    """A failure while creating the temp file raises EmitError and writes nothing
    at out_path — fail-closed with no partial artefact (Requirement 6.8)."""
    rep = _content_full_report()
    perf_dir = tmp_path / "docs" / "perf"
    perf_dir.mkdir(parents=True)
    out_path = perf_dir / "posture_scores.md"

    def _boom(*args, **kwargs):
        raise OSError("simulated tempfile.mkstemp failure")

    monkeypatch.setattr(report.tempfile, "mkstemp", _boom)

    with pytest.raises(report.EmitError):
        report.emit(rep, out_path)

    assert not out_path.exists()
    leftovers = list(perf_dir.iterdir())
    assert leftovers == [], f"expected no leftover files, found {leftovers!r}"


def test_emit_dir_creation_failure_raises_emit_error(tmp_path, monkeypatch):
    """A failure to create the docs/perf parent directory raises EmitError and
    leaves no report file (Requirement 6.8)."""
    rep = _content_full_report()
    out_path = tmp_path / "docs" / "perf" / "posture_scores.md"

    orig_mkdir = report.Path.mkdir

    def _boom(self, *args, **kwargs):
        raise OSError("simulated mkdir failure")

    monkeypatch.setattr(report.Path, "mkdir", _boom)

    with pytest.raises(report.EmitError):
        report.emit(rep, out_path)

    monkeypatch.setattr(report.Path, "mkdir", orig_mkdir)
    assert not out_path.exists()
