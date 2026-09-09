"""CLI entrypoint for the posture scoring harness (the Reproducible_Command target).

Wires the measurement pipeline: resolve attribution, load the corpus read-only,
score each posture, select a threshold on the train split, compute metrics on the
scored set, and emit the report. Headless, no interactive input (Requirement 7.1).

Documented command:
    cd gateway && ./.venv/bin/python ../scripts/detection/score_postures.py

Task 9.1 (this change) implements the pipeline orchestration and the CLI. The
orchestration is:

    resolve attribution  (abort, no report, if any value is missing — R6.10)
      -> load_corpus      (abort, no report, if unreadable or 0 items — R7.3, R1.6)
      -> build the scored set (eval | full — R6.7)
      -> for each posture:
           probe()  -> unavailable: record a "not scored" result + reason (R8.1)
                    -> available:   score the TRAIN split, select_threshold on
                                    train (no eval leakage — R7.5/7.6), apply the
                                    Selected_Threshold to the SCORED SET, compute
                                    recall / FPR / per-family / paraphrase-gap
                                    (round at the reporting boundary — R2.8)
      -> build_report      (abort, no report, if all three unavailable — R8.5)
      -> emit report       (fail closed on a write error — R6.8)

The module imports its sibling modules with the same try/except dual-import style
the other modules use, so it works both run-as-a-script from ``gateway/`` (bare
``import corpus`` with ``scripts/detection`` on ``sys.path``) and imported as the
``scripts.detection`` package by the tests. ``main(argv=None)`` is importable and
callable directly from tests; only the ``__main__`` guard calls ``sys.exit``.

Zero blast radius (Requirement 9): this module touches only ``scripts/detection/**``
and emits under ``docs/perf/**`` at run time; it never modifies ``scanner.py`` or
any production path, and it invokes the scanner read-only via the posture adapters.

Exit-code / fail-closed refinement beyond a reasonable first cut is Task 9.2; the
CLI-operability / exit-code tests are Task 9.3.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

try:  # script context: run from gateway/ with scripts/detection on sys.path
    import attribution as _attribution
    import corpus as _corpus
    import metrics as _metrics
    import postures as _postures
    import report as _report
    import thresholds as _thresholds
except ImportError:  # package context: imported as scripts.detection.score_postures
    from . import attribution as _attribution
    from . import corpus as _corpus
    from . import metrics as _metrics
    from . import postures as _postures
    from . import report as _report
    from . import thresholds as _thresholds


# The repository root is two directories above this file:
#   <repo_root>/scripts/detection/score_postures.py
# so ``parents[2]`` is ``<repo_root>``. The corpus lives at
# ``<repo_root>/tests/detection_corpus`` and the report is emitted to
# ``<repo_root>/docs/perf/posture_scores.md`` (Design "component / file layout").
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORPUS_RELATIVE_PATH = Path("tests") / "detection_corpus"
_REPORT_RELATIVE_PATH = Path("docs") / "perf" / "posture_scores.md"


def _ensure_cwd_importable() -> None:
    """Make the current working directory importable so ``ai_mesh_gateway`` resolves.

    The documented Reproducible_Command is
    ``cd gateway && ./.venv/bin/python ../scripts/detection/score_postures.py``.
    ``ai_mesh_gateway`` is NOT pip-installed in ``gateway/.venv``; it resolves only
    because it lives under the ``gateway/`` working directory. But when Python runs
    a script BY PATH it sets ``sys.path[0]`` to the SCRIPT'S directory
    (``scripts/detection/``) and does NOT add the current working directory to
    ``sys.path`` (only ``python -c`` and ``python -m`` add cwd/'' to it). So the
    posture adapters' ``from ai_mesh_gateway import scanner`` (and the bare
    ``import scanner`` fallback) would fail with ModuleNotFoundError and every
    posture would be recorded unavailable → an all-unavailable abort with no report
    (R8.5), even though the scanner is perfectly importable from ``gateway/``.

    Prepend ``os.getcwd()`` to ``sys.path`` (only if not already present, without
    removing or reordering existing entries, and without hard-coding any absolute
    path) so the DOCUMENTED command's cwd — ``gateway/`` — is importable. This does
    not weaken the fail-closed contract: run from an unrelated cwd where the scanner
    genuinely cannot be imported, the postures still fail closed honestly (R8.5);
    this only ensures the documented command's cwd is on ``sys.path``. It also does
    not break the package-context sibling imports (``from . import ...``), which
    resolve via the already-installed package, not via cwd.
    """
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)


def _repo_root() -> Path:
    """Return the repository root (``<repo_root>``) resolved from this file's path."""
    return _REPO_ROOT


def _corpus_root(repo_root: Path) -> Path:
    """Return the read-only Detection_Corpus directory under ``repo_root``."""
    return repo_root / _CORPUS_RELATIVE_PATH


def _report_path(repo_root: Path) -> Path:
    """Return the Posture_Report output path under ``repo_root`` (docs/perf/**)."""
    return repo_root / _REPORT_RELATIVE_PATH


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the CLI arguments. The only documented argument selects the scored set.

    ``--scored-set {eval,full}`` defaults to ``eval`` (the headline is measured on
    the eval split, Requirement 6.7). No interactive input, no other arguments
    (Requirement 7.1).
    """
    parser = argparse.ArgumentParser(
        prog="score_postures.py",
        description=(
            "Score the three detection postures of the shipped scanner over the "
            "labelled detection corpus and emit docs/perf/posture_scores.md. "
            "Measurement-only; the scanner is invoked read-only."
        ),
    )
    parser.add_argument(
        "--scored-set",
        choices=["eval", "full"],
        default="eval",
        help=(
            "Which set to compute metrics over: 'eval' (the headline eval split, "
            "default) or 'full' (the whole corpus)."
        ),
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _train_triples(
    scored_set_items: Sequence[_corpus.Corpus_Item],
) -> list[_corpus.Corpus_Item]:
    """Return the train-split items used for threshold tuning (no eval leakage).

    Threshold tuning consumes ONLY items whose split is ``train`` (Requirements
    7.5, 7.6); the Selected_Threshold is then applied to the scored set. Note the
    train split is always drawn from the FULL corpus, independent of the scored
    set, so the tuning population never depends on whether the headline is
    measured on ``eval`` or ``full``.
    """
    return [item for item in scored_set_items if _corpus.split_of(item) == _corpus.TRAIN_SPLIT]


def _score_items(
    posture: _postures.PostureAdapter, items: Sequence[_corpus.Corpus_Item]
) -> list[float]:
    """Score every item's text with ``posture`` (read-only ``text -> score``)."""
    return [posture.score(item.text) for item in items]


def _labels_of(items: Sequence[_corpus.Corpus_Item]) -> list[str]:
    """Return the ground-truth labels, read ONLY from each item's ``label`` field."""
    return [item.label for item in items]


def _families_of(items: Sequence[_corpus.Corpus_Item]) -> list[str]:
    """Return the family of each item, positionally aligned with its score/label."""
    return [item.family for item in items]


def _family_counts(items: Sequence[_corpus.Corpus_Item]) -> dict[str, int]:
    """Count corpus items per family over ``items`` (the malicious/benign backing count).

    Only recognized-label items contribute a count for their family, so the count
    backing a per-family recall cell is the family's malicious population and the
    count backing a per-family FPR cell is the family's benign population
    (Requirements 4.4, 5.4). Items with an unrecognized label are excluded here
    (they are neither malicious nor benign) and are reported via the scored-set
    excluded-label count instead.
    """
    counts: dict[str, int] = {}
    for item in items:
        if item.label not in (_corpus.MALICIOUS_LABEL, _corpus.BENIGN_LABEL):
            continue
        counts[item.family] = counts.get(item.family, 0) + 1
    return counts


def _round_cells(
    cells: dict[str, "object"],
) -> dict[str, object]:
    """Round every computable per-family cell to 4 dp; pass NotComputable through.

    ``per_family_recall`` / ``per_family_fpr`` already round their computable
    values, but rounding here as well is idempotent (Property 6) and keeps the
    reporting boundary explicit — a :class:`~metrics.NotComputable` cell is passed
    through unchanged so a "not computable" cell stays distinguishable from a
    metric value (Requirements 4.5, 5.5, Property 14).
    """
    rounded: dict[str, object] = {}
    for family, cell in cells.items():
        if isinstance(cell, _metrics.NotComputable):
            rounded[family] = cell
        else:
            rounded[family] = _metrics.round_metric(cell)
    return rounded


def _score_posture(
    posture: _postures.PostureAdapter,
    scored_items: Sequence[_corpus.Corpus_Item],
) -> _report.PostureResult:
    """Score one posture over the scored set and build its :class:`PostureResult`.

    Probes availability first; an unavailable posture is recorded "not scored" with
    its non-empty reason and no metric values (Requirements 8.1, 8.2). An available
    posture is scored on the TRAIN split, its Selected_Threshold is chosen on train
    only (Requirements 3, 7.5, 7.6), and that threshold is applied to the SCORED SET
    to compute recall / FPR / per-family breakdowns / paraphrase gap, rounded at the
    reporting boundary (Requirement 2.8). When the target FPR is not achievable the
    honest FPR-floor form is reported instead of a Recall_At_Target_FPR
    (Requirement 3.5).
    """
    availability = posture.probe()
    if not availability.available:
        # Unavailable posture: "not scored" + reason, no metric values (R8.1, 8.2).
        return _report.PostureResult(
            posture_name=posture.name,
            status=_report.STATUS_NOT_SCORED,
            not_scored_reason=availability.reason,
        )

    # --- threshold tuning on the TRAIN split only (no eval leakage) ---------
    train_items = _train_triples(scored_items)
    train_scores = _score_items(posture, train_items)
    train_labels = _labels_of(train_items)
    choice = _thresholds.select_threshold(train_scores, train_labels)
    selected_threshold = choice.selected_threshold

    # --- apply the Selected_Threshold to the SCORED SET for measurement -----
    scored_scores = _score_items(posture, scored_items)
    scored_labels = _labels_of(scored_items)
    scored_families = _families_of(scored_items)

    recall_value = _metrics.recall(scored_scores, scored_labels, selected_threshold)
    fpr_value = _metrics.fpr(scored_scores, scored_labels, selected_threshold)

    per_family_recall = _round_cells(
        _metrics.per_family_recall(
            scored_scores, scored_labels, scored_families, selected_threshold
        )
    )
    per_family_fpr = _round_cells(
        _metrics.per_family_fpr(
            scored_scores, scored_labels, scored_families, selected_threshold
        )
    )
    family_counts = _family_counts(scored_items)

    gap = _metrics.paraphrase_gap(per_family_recall)
    paraphrase_gap = gap if isinstance(gap, float) else None

    # Achieved FPR at the selected threshold over the scored set (4 dp); when the
    # scored set has no benign items the FPR is not computable → None.
    achieved_fpr = (
        _metrics.round_metric(fpr_value) if isinstance(fpr_value, float) else None
    )

    if choice.target_achievable:
        # Headline Recall_At_Target_FPR at the selected threshold (R3.4), 4 dp.
        recall_at_target = (
            _metrics.round_metric(recall_value)
            if isinstance(recall_value, float)
            else None
        )
        return _report.PostureResult(
            posture_name=posture.name,
            status=_report.STATUS_SCORED,
            recall_at_target_fpr=recall_at_target,
            selected_threshold=_metrics.round_metric(selected_threshold),
            achieved_fpr=achieved_fpr,
            target_fpr_achievable=True,
            per_family_recall=per_family_recall,
            per_family_fpr=per_family_fpr,
            family_counts=family_counts,
            paraphrase_gap=paraphrase_gap,
        )

    # Target FPR not achievable → honest FPR-floor form (R3.5). The floor and the
    # recall at the floor threshold are derived from the TRAIN split (the tuning
    # population), consistent with select_threshold's own floor branch.
    floor = _thresholds.fpr_floor(train_scores, train_labels)
    fpr_floor_value = (
        _metrics.round_metric(floor.fpr_floor)
        if isinstance(floor.fpr_floor, float)
        else None
    )
    recall_at_floor = _metrics.round_metric(floor.recall_at_floor)
    return _report.PostureResult(
        posture_name=posture.name,
        status=_report.STATUS_SCORED,
        recall_at_target_fpr=None,
        selected_threshold=_metrics.round_metric(selected_threshold),
        achieved_fpr=achieved_fpr,
        target_fpr_achievable=False,
        fpr_floor=fpr_floor_value,
        recall_at_floor=recall_at_floor,
        per_family_recall=per_family_recall,
        per_family_fpr=per_family_fpr,
        family_counts=family_counts,
        paraphrase_gap=paraphrase_gap,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the posture scoring pipeline; return a process exit code.

    Exit-code / fail-closed contract (Task 9.2, Requirements 1.6, 6.8, 6.10,
    7.3, 8.5). The function returns 0 ONLY when a report was emitted, and a single
    non-zero code (1) on EVERY fail-closed condition; a non-zero return always
    means NO report was written (or, for a write failure, no partial/empty report
    was left behind). A single non-zero code is acceptable per the spec (it asks
    for a "failure exit indication"); each failure class is instead disambiguated
    by a distinct, cause-naming message on stderr. The failure classes are:

    * missing attribution — corpus version / reproducible command / target FPR
      cannot be determined (R6.10). Resolved FIRST, before any posture is probed
      or scored, so a missing attribution never triggers scanner side effects.
    * unreadable or empty corpus — the corpus cannot be read, is malformed, or
      contains 0 items, or the selected scored set is empty (R7.3, R1.6). Checked
      before any scoring.
    * invalid posture configuration — a duplicate or invalid posture name (R1.5);
      rejected before any scoring.
    * all three postures unavailable — no posture could be scored, whether because
      each probed unavailable or because each raised while scoring (R8.5).
    * report-write failure — ``emit`` could not write the report atomically; its
      atomic temp-file + ``os.replace`` + cleanup guarantees no partial or empty
      report file is left behind (R6.8).

    Ordering guarantees (so no code path can emit a report when a required
    precondition failed): attribution is resolved before corpus load; corpus
    emptiness is checked before scoring; the report is assembled and emitted ONLY
    after ``build_report`` succeeds AND at least one posture was scored. A scoring
    exception for a single posture does NOT abort the whole run — that posture is
    recorded "not scored" with the exception as its reason so the remaining
    postures still report (aligns with R8.4); only when EVERY posture ends up not
    scored does the run fail closed under R8.5.
    """
    args = _parse_args(argv)
    which = args.scored_set

    # Make the documented command's cwd (gateway/) importable so the posture
    # adapters can resolve ``ai_mesh_gateway`` — running a script BY PATH drops cwd
    # from sys.path. Done BEFORE any posture is built/probed. See
    # _ensure_cwd_importable for the full rationale and the fail-closed caveat.
    _ensure_cwd_importable()

    repo_root = _repo_root()
    corpus_root = _corpus_root(repo_root)
    out_path = _report_path(repo_root)

    # --- resolve attribution first; a missing value aborts with no report ----
    attribution = _attribution.resolve_attribution(repo_root)
    if isinstance(attribution, _attribution.AttributionUnavailable):
        print(
            f"error: cannot determine attribution value {attribution.value!r}: "
            f"{attribution.reason}; no report emitted",
            file=sys.stderr,
        )
        return 1

    # --- load the corpus read-only; unreadable or empty aborts with no report -
    try:
        corpus = _corpus.load_corpus(corpus_root)
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
        print(
            f"error: could not read the detection corpus at {corpus_root!s}: "
            f"{exc}; no report emitted",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        # Malformed corpus content (e.g. a bad JSONL line) is an unreadable corpus.
        print(
            f"error: the detection corpus at {corpus_root!s} is malformed: {exc}; "
            "no report emitted",
            file=sys.stderr,
        )
        return 1

    if len(corpus.items) == 0:
        print(
            f"error: the detection corpus at {corpus_root!s} contains 0 items; "
            "no report emitted",
            file=sys.stderr,
        )
        return 1

    # --- build the scored set (eval | full) ----------------------------------
    scored = _corpus.scored_set(corpus, which)
    scored_items = scored.items

    if len(scored_items) == 0:
        print(
            f"error: the scored set {which!r} is empty; no report emitted",
            file=sys.stderr,
        )
        return 1

    # --- build the postures (validated names) --------------------------------
    try:
        postures = _postures.build_postures()
    except _postures.DuplicatePostureNameError as exc:
        print(
            f"error: duplicate posture name {exc.name!r}; no report emitted",
            file=sys.stderr,
        )
        return 1
    except _postures.InvalidPostureNameError as exc:
        print(f"error: {exc}; no report emitted", file=sys.stderr)
        return 1

    # --- score every posture over the scored set -----------------------------
    # A scoring/scanner exception for ONE posture must not crash the whole run:
    # record that posture "not scored" with the exception as its reason so the
    # other postures still report (R8.4), then let the all-unavailable check below
    # fail closed under R8.5 only if EVERY posture ended up not scored. The reason
    # is coerced non-empty because PostureResult requires a non-empty reason for a
    # not-scored posture (R8.1); a scoring exception yields a "not scored" row, not
    # a fabricated metric value.
    results: list[_report.PostureResult] = []
    any_scored = False
    for posture in postures:
        try:
            result = _score_posture(posture, scored_items)
        except Exception as exc:  # scanner/scoring failure for THIS posture only
            reason = (
                f"scoring failed ({type(exc).__name__}: {exc})"
                if str(exc)
                else f"scoring failed ({type(exc).__name__})"
            )
            print(
                f"warning: posture {posture.name!r} could not be scored: {reason}; "
                "recording it as not scored",
                file=sys.stderr,
            )
            result = _report.PostureResult(
                posture_name=posture.name,
                status=_report.STATUS_NOT_SCORED,
                not_scored_reason=reason,
            )
        results.append(result)
        if result.is_scored:
            any_scored = True

    # --- all three unavailable → error, no report (R8.5) ---------------------
    if not any_scored:
        reasons = "; ".join(
            f"{result.posture_name}: {result.not_scored_reason}"
            for result in results
        )
        print(
            f"error: no posture could be scored ({reasons}); no report emitted",
            file=sys.stderr,
        )
        return 1

    # --- scored-set metadata + counts ----------------------------------------
    label_partition = _corpus.partition(scored_items)
    excluded_split_count = sum(
        1 for item in scored_items if _corpus.split_of(item) == "other"
    )
    scored_set_meta = _report.ScoredSetMeta(
        which=which,
        malicious_count=label_partition.malicious_count,
        benign_count=label_partition.benign_count,
        excluded_label_count=label_partition.excluded_count,
        excluded_split_count=excluded_split_count,
    )

    # --- assemble the report -------------------------------------------------
    # build_report is pure and performs no I/O; it can only fail on a programming
    # invariant (e.g. wrong posture count / a mis-typed attribution). Fail closed
    # with a non-zero exit and NO report rather than crash with a traceback, so no
    # partial or fabricated report is ever emitted (R6.8, R6.10).
    try:
        report = _report.build_report(results, scored_set_meta, attribution)
    except (ValueError, TypeError) as exc:
        print(
            f"error: could not assemble the posture report: {exc}; "
            "no report emitted",
            file=sys.stderr,
        )
        return 1

    # --- emit the report (atomic; fail closed on a write error) --------------
    try:
        _report.emit(report, out_path)
    except _report.EmitError as exc:
        print(
            f"error: could not write the posture report to {out_path!s}: {exc}; "
            "no partial report left behind",
            file=sys.stderr,
        )
        return 1

    print(f"wrote posture report to {out_path!s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
