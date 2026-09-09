"""Report model and emitter for the posture scoring harness.

Assembles the ``PostureReport`` (exactly one entry per posture, scored or "not
scored") and atomically emits the committed plain-text report to
``docs/perf/posture_scores.md`` (Requirements 6, 8, 10).

This module defines the report data models (``PostureResult``, ``ScoredSetMeta``,
``PostureReport``) and the pure :func:`build_report` assembly. The assembly takes
the per-posture results (including "not scored" ones, each carrying a non-empty
reason), the scored-set metadata, and the resolved :class:`~attribution.Attribution`
and produces a ``PostureReport`` with **exactly three** posture entries and the
threshold-selection rule text (including "tuned on train split only").

Assembly is side-effect-free and fully unit-testable. The atomic ``emit`` to
``docs/perf/`` is a separate concern (owned by its own task) and is left as a
stub here — :func:`build_report` never performs any I/O.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence, Union

try:  # script context: run from scripts/detection/ (the reproducible command)
    from attribution import Attribution, AttributionUnavailable
    from metrics import NotComputable
except ImportError:  # package context: imported as scripts.detection.report
    from .attribution import Attribution, AttributionUnavailable
    from .metrics import NotComputable

# Number of detection postures the report always describes — exactly one entry
# per posture, three total (Requirements 6.9, 8.3, Property 15). ``build_report``
# rejects any ``results`` list that does not name exactly this many distinct
# postures so a report can never silently omit or duplicate a posture.
EXPECTED_POSTURE_COUNT = 3

# Status values for a posture entry (Requirement 8.3). A ``scored`` posture
# carries metric values; a ``not_scored`` posture carries a non-empty reason and
# no metric values, in a form distinguishable from a metric value.
STATUS_SCORED = "scored"
STATUS_NOT_SCORED = "not_scored"

# The threshold-selection rule text recorded in every report. It states both the
# recall-maximising rule subject to the target FPR AND that tuning consumed the
# train split only (Requirements 3.8, 7.5, 7.6) — the "tuned on train split only"
# phrase is mandatory and asserted by the report-content tests.
THRESHOLD_RULE_TEXT = (
    "Selected_Threshold maximises recall subject to FPR <= Target_FPR "
    "(tolerance 1e-6), tie-broken by lowest FPR then highest threshold; "
    "candidate thresholds are the distinct posture scores over the train split; "
    "tuned on train split only, then applied to the scored set for measurement."
)

# A per-family cell is either a computed proportion (a real ``float`` in the
# family's metric range) or a :class:`~metrics.NotComputable` sentinel. The
# sentinel is deliberately NOT a ``float`` so a "not computable" cell is always
# distinguishable from a metric value (Requirements 4.5, 5.5, 10.1, Property 14).
FamilyCell = Union[float, NotComputable]

# The explicit marker rendered for a per-family cell that is not computable — a
# family present in ``families.json`` with 0 items in the scored set. A cell is
# NEVER left blank: a :class:`~metrics.NotComputable` sentinel renders as this
# marker so a reader can always distinguish "no data" from a computed value
# (Requirements 4.5, 5.5, 10.1, Property 14).
NOT_COMPUTABLE_MARKER = "not computable"


@dataclass(frozen=True)
class PostureResult:
    """One posture's row in the report — scored (with metrics) or not scored.

    A ``not_scored`` posture carries a non-empty ``not_scored_reason`` and no
    metric values: ``recall_at_target_fpr`` / ``selected_threshold`` /
    ``achieved_fpr`` / ``fpr_floor`` / ``recall_at_floor`` / ``paraphrase_gap`` are
    ``None`` and the per-family maps are empty — so its "no metric value" state is
    always distinguishable from a real metric value (Requirements 8.1–8.4,
    Property 15).

    A ``scored`` posture reports the headline ``recall_at_target_fpr`` (4 dp) when
    the target FPR was achievable, or the FPR-floor form (``fpr_floor`` +
    ``recall_at_floor`` with ``target_fpr_achievable = False``) when it was not
    (Requirement 3.5). ``per_family_recall`` covers every attack family present
    (incl. ``paraphrase``) and ``per_family_fpr`` every benign family present
    (incl. ``developer_traffic``); a present family with 0 items is represented by
    a :class:`~metrics.NotComputable` cell, never omitted (Requirements 4.3–4.5,
    5.3–5.5, 10.1). ``family_counts`` records the per-family item count backing
    each cell (Requirements 4.4, 5.4).
    """

    posture_name: str
    status: str
    not_scored_reason: str | None = None
    recall_at_target_fpr: float | None = None
    selected_threshold: float | None = None
    achieved_fpr: float | None = None
    target_fpr_achievable: bool = False
    fpr_floor: float | None = None
    recall_at_floor: float | None = None
    per_family_recall: dict[str, FamilyCell] = field(default_factory=dict)
    per_family_fpr: dict[str, FamilyCell] = field(default_factory=dict)
    family_counts: dict[str, int] = field(default_factory=dict)
    paraphrase_gap: float | None = None

    def __post_init__(self) -> None:
        if not self.posture_name:
            raise ValueError("PostureResult.posture_name must be a non-empty string")
        if self.status not in (STATUS_SCORED, STATUS_NOT_SCORED):
            raise ValueError(
                f"PostureResult.status must be {STATUS_SCORED!r} or "
                f"{STATUS_NOT_SCORED!r}, got {self.status!r}"
            )
        if self.status == STATUS_NOT_SCORED:
            # A not-scored posture MUST carry a non-empty reason and MUST NOT carry
            # any metric value (Requirements 8.1, 8.2, 8.4, Property 15).
            if not self.not_scored_reason:
                raise ValueError(
                    f"not-scored posture {self.posture_name!r} must carry a "
                    "non-empty not_scored_reason"
                )
            _forbidden = (
                self.recall_at_target_fpr,
                self.selected_threshold,
                self.achieved_fpr,
                self.fpr_floor,
                self.recall_at_floor,
                self.paraphrase_gap,
            )
            if any(value is not None for value in _forbidden) or (
                self.per_family_recall or self.per_family_fpr
            ):
                raise ValueError(
                    f"not-scored posture {self.posture_name!r} must carry no "
                    "metric values"
                )

    @property
    def is_scored(self) -> bool:
        """Whether this posture was scored (carries metric values)."""
        return self.status == STATUS_SCORED


@dataclass(frozen=True)
class ScoredSetMeta:
    """The scored set a run computed its metrics over, with its counts.

    ``which`` is ``"eval"`` (the headline split) or ``"full"`` (the whole corpus)
    (Requirements 6.4, 6.7). ``malicious_count`` / ``benign_count`` are the
    recognized-label populations backing recall / FPR (Requirements 2.6, 6.4).
    ``excluded_label_count`` is the number of items with an absent/unrecognized
    ``label`` (Requirement 2.7) and ``excluded_split_count`` the number with a
    ``split`` that is neither ``train`` nor ``eval`` (Requirement 7.7); both are
    reported so a reader can reconcile the counts against the total corpus.
    """

    which: str
    malicious_count: int
    benign_count: int
    excluded_label_count: int = 0
    excluded_split_count: int = 0

    def __post_init__(self) -> None:
        if self.which not in ("eval", "full"):
            raise ValueError(
                f"ScoredSetMeta.which must be 'eval' or 'full', got {self.which!r}"
            )
        for name in (
            "malicious_count",
            "benign_count",
            "excluded_label_count",
            "excluded_split_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"ScoredSetMeta.{name} must be non-negative")


@dataclass(frozen=True)
class PostureReport:
    """The complete schema of ``docs/perf/posture_scores.md``.

    Contains **exactly three** posture entries (Requirement 8.3, Property 15), the
    scored-set metadata + counts (Requirement 6.4), the corpus version
    (Requirement 6.5), the reproducible command + target FPR (Requirement 6.6),
    and the threshold-selection rule text including "tuned on train split only"
    (Requirement 3.8). Every rendered number traces to one of these fields — no
    foreign constant is introduced at emit time (Requirement 7.8).
    """

    postures: tuple[PostureResult, ...]
    scored_set: ScoredSetMeta
    corpus_version: str
    reproducible_command: str
    target_fpr: float
    threshold_rule: str = THRESHOLD_RULE_TEXT

    def __post_init__(self) -> None:
        if len(self.postures) != EXPECTED_POSTURE_COUNT:
            raise ValueError(
                f"PostureReport must contain exactly {EXPECTED_POSTURE_COUNT} "
                f"posture entries, got {len(self.postures)}"
            )
        names = [result.posture_name for result in self.postures]
        if len(set(names)) != len(names):
            raise ValueError(
                f"PostureReport posture names must be unique, got {names!r}"
            )
        if not self.corpus_version:
            raise ValueError("PostureReport.corpus_version must be non-empty")
        if not self.reproducible_command:
            raise ValueError("PostureReport.reproducible_command must be non-empty")
        if not self.threshold_rule:
            raise ValueError("PostureReport.threshold_rule must be non-empty")


def build_report(
    results: Sequence[PostureResult],
    scored_set_meta: ScoredSetMeta,
    attribution: Attribution,
) -> PostureReport:
    """Assemble the :class:`PostureReport` from per-posture results + attribution.

    ``results`` is the per-posture outcome list — one :class:`PostureResult` per
    posture, including any that were "not scored" (each carrying a non-empty
    reason). ``scored_set_meta`` describes the scored set and its counts.
    ``attribution`` is the fully-resolved :class:`~attribution.Attribution`
    (corpus version, reproducible command, target FPR) — a run cannot build a
    report without it (Requirement 6.10), so an :class:`AttributionUnavailable` is
    rejected here rather than allowed to leak an unattributed number.

    The assembled report always carries **exactly three** posture entries
    (Requirement 8.3, Property 15) and the threshold-selection rule text including
    "tuned on train split only" (Requirement 3.8). It records the scored set +
    counts (Requirement 6.4), the corpus version (Requirement 6.5), and the
    reproducible command + target FPR (Requirement 6.6). The per-posture metric
    content — headline recall or FPR-floor form, per-family recall/FPR cells with
    their counts, and the paraphrase gap — is carried through unchanged from each
    :class:`PostureResult`; not-computable cells arrive as
    :class:`~metrics.NotComputable`, distinguishable from a float (Property 14).

    Pure: performs no I/O. Emitting the report to ``docs/perf/`` is a separate
    concern (see :func:`emit`).

    Raises :class:`TypeError` when ``attribution`` is not a resolved
    :class:`~attribution.Attribution`, and :class:`ValueError` when ``results``
    does not name exactly three distinct postures (the ``PostureReport`` invariant
    enforces the count and uniqueness).
    """
    if isinstance(attribution, AttributionUnavailable):
        raise TypeError(
            "build_report requires a resolved Attribution; got "
            f"AttributionUnavailable for {attribution.value!r} "
            f"({attribution.reason}). A report must not be assembled with a "
            "missing attribution value (Requirement 6.10)."
        )
    if not isinstance(attribution, Attribution):
        raise TypeError(
            f"build_report attribution must be an Attribution, got "
            f"{type(attribution).__name__}"
        )

    postures = tuple(results)
    # The PostureReport invariant enforces "exactly three, unique names"; surface a
    # clearer error here before constructing it so the caller learns which count it
    # supplied (Requirements 8.3, 6.9).
    if len(postures) != EXPECTED_POSTURE_COUNT:
        raise ValueError(
            f"build_report requires exactly {EXPECTED_POSTURE_COUNT} posture "
            f"results (one per posture), got {len(postures)}"
        )

    return PostureReport(
        postures=postures,
        scored_set=scored_set_meta,
        corpus_version=attribution.corpus_version,
        reproducible_command=attribution.reproducible_command,
        target_fpr=attribution.target_fpr,
        threshold_rule=THRESHOLD_RULE_TEXT,
    )


class EmitError(Exception):
    """Non-success signal that the report could not be written atomically.

    Raised by :func:`emit` on any write/replace failure. It guarantees the caller
    (the CLI) can fail closed with a non-zero exit and that no partial or empty
    report file was left behind (Requirement 6.8): the rendered text is written to
    a temporary file in the destination directory and only ``os.replace``d into the
    final path once fully written, and the temporary file is removed on any error.
    """


def _fmt_metric(value: float) -> str:
    """Render an already-rounded (4 dp) metric value as a fixed-width string.

    The metric functions round to 4 decimal places via
    :func:`~metrics.round_metric`; formatting with ``%.4f`` keeps the rendered
    number byte-identical on re-runs (Requirement 10.3) and traces directly to the
    computed field (no foreign constant, Requirement 7.8).
    """
    return f"{value:.4f}"


def _fmt_cell(cell: FamilyCell) -> str:
    """Render a per-family cell — a metric value or the not-computable marker.

    A real ``float`` renders as its 4 dp value; a :class:`~metrics.NotComputable`
    renders as :data:`NOT_COMPUTABLE_MARKER`, so a not-computable cell is always
    explicit and never blank (Requirements 4.5, 5.5, 10.1, Property 14).
    """
    if isinstance(cell, NotComputable):
        return NOT_COMPUTABLE_MARKER
    return _fmt_metric(cell)


def _render_family_table(
    title: str,
    count_label: str,
    cells: dict[str, FamilyCell],
    counts: dict[str, int],
) -> list[str]:
    """Render one per-family table (recall or FPR) as Markdown lines.

    Every family key present in ``cells`` gets a row carrying its rendered cell
    value and its backing count from ``counts`` (0 when absent). A cell is never
    left blank — a :class:`~metrics.NotComputable` renders as the explicit marker
    (Requirements 4.3, 4.4, 5.3, 5.4, 10.1).
    """
    lines = [f"#### {title}", "", f"| Family | {count_label} | Value |", "| --- | --- | --- |"]
    for family in sorted(cells):
        count = counts.get(family, 0)
        lines.append(f"| {family} | {count} | {_fmt_cell(cells[family])} |")
    lines.append("")
    return lines


def _render_posture(result: PostureResult) -> list[str]:
    """Render one posture's section (scored or not scored) as Markdown lines."""
    lines = [f"### Posture: {result.posture_name}", ""]

    if not result.is_scored:
        # A not-scored posture carries its non-empty reason and NO metric value, in
        # a form clearly distinct from a number (Requirements 8.1, 8.2, 8.4).
        lines.append(f"- Status: **not scored**")
        lines.append(f"- Reason: {result.not_scored_reason}")
        lines.append("")
        return lines

    lines.append("- Status: **scored**")

    # Headline operating point: the Recall_At_Target_FPR when the target was
    # achievable, otherwise the honest FPR-floor form (Requirement 3.5).
    if result.target_fpr_achievable and result.recall_at_target_fpr is not None:
        lines.append(
            f"- Recall_At_Target_FPR: {_fmt_metric(result.recall_at_target_fpr)}"
        )
    else:
        floor = (
            _fmt_metric(result.fpr_floor)
            if result.fpr_floor is not None
            else NOT_COMPUTABLE_MARKER
        )
        recall_at_floor = (
            _fmt_metric(result.recall_at_floor)
            if result.recall_at_floor is not None
            else NOT_COMPUTABLE_MARKER
        )
        lines.append("- Target_FPR not achievable; reporting FPR floor:")
        lines.append(f"  - FPR_Floor: {floor}")
        lines.append(f"  - Recall_At_Floor: {recall_at_floor}")

    threshold = (
        _fmt_metric(result.selected_threshold)
        if result.selected_threshold is not None
        else NOT_COMPUTABLE_MARKER
    )
    achieved_fpr = (
        _fmt_metric(result.achieved_fpr)
        if result.achieved_fpr is not None
        else NOT_COMPUTABLE_MARKER
    )
    lines.append(f"- Selected_Threshold: {threshold}")
    lines.append(f"- Achieved_FPR: {achieved_fpr}")

    if result.paraphrase_gap is not None:
        lines.append(f"- Paraphrase_Gap: {_fmt_metric(result.paraphrase_gap)}")
    else:
        lines.append(f"- Paraphrase_Gap: {NOT_COMPUTABLE_MARKER}")
    lines.append("")

    lines.extend(
        _render_family_table(
            "Per-family recall (malicious)",
            "Malicious count",
            result.per_family_recall,
            result.family_counts,
        )
    )
    lines.extend(
        _render_family_table(
            "Per-family FPR (benign)",
            "Benign count",
            result.per_family_fpr,
            result.family_counts,
        )
    )
    return lines


def render(report: PostureReport) -> str:
    """Render ``report`` to the human-readable plain-text/Markdown report string.

    Every rendered number traces to a field of ``report`` — no foreign constant is
    introduced at render time (Requirement 7.8). The output states: per posture the
    scored/not-scored status and (when scored) the headline recall or FPR-floor
    form, Selected_Threshold + achieved FPR, per-family recall/FPR tables with their
    counts, and the paraphrase gap; and report-level the scored set (eval/full) with
    malicious/benign + excluded counts, the corpus version, the reproducible command
    + target FPR, and the threshold-selection rule text (incl. "tuned on train split
    only") (Requirements 3.8, 4.3–4.5, 5.3–5.5, 6.4–6.6, 8.1–8.4, 10.1).
    """
    lines: list[str] = ["# Posture Scores", ""]

    # Report-level attribution + scored-set context (Requirements 6.4, 6.5, 6.6).
    lines.append(f"- Corpus_Version: {report.corpus_version}")
    lines.append(f"- Reproducible_Command: `{report.reproducible_command}`")
    lines.append(f"- Target_FPR: {report.target_fpr}")
    lines.append("")

    meta = report.scored_set
    lines.append("## Scored set")
    lines.append("")
    lines.append(f"- Scored_Set: {meta.which}")
    lines.append(f"- Malicious_Count: {meta.malicious_count}")
    lines.append(f"- Benign_Count: {meta.benign_count}")
    lines.append(f"- Excluded_Label_Count: {meta.excluded_label_count}")
    lines.append(f"- Excluded_Split_Count: {meta.excluded_split_count}")
    lines.append("")

    # Threshold-selection rule, including the mandatory "tuned on train split only"
    # phrasing (Requirements 3.8, 7.5, 7.6).
    lines.append("## Threshold selection rule")
    lines.append("")
    lines.append(report.threshold_rule)
    lines.append("")

    lines.append("## Postures")
    lines.append("")
    for result in report.postures:
        lines.extend(_render_posture(result))

    # Trailing newline so the file ends cleanly.
    return "\n".join(lines) + "\n"


def emit(report: PostureReport, out_path: Path) -> None:
    """Atomically write ``report`` to ``out_path`` (``docs/perf/posture_scores.md``).

    Renders ``report`` via :func:`render`, ensures the parent ``docs/perf/``
    directory exists, writes the rendered text to a TEMPORARY file in the SAME
    directory (so the final ``os.replace`` is an atomic same-filesystem rename),
    flushes + ``fsync``s, then ``os.replace``s the temp file into ``out_path``. The
    replace is atomic: a reader ever sees either the previous report or the complete
    new one, never a half-written file.

    On ANY write/replace error the temporary file is removed and an
    :class:`EmitError` is raised, so the caller fails closed with no partial or empty
    report left behind (Requirement 6.8). No metric value is fabricated: the file is
    the byte-for-byte :func:`render` of ``report``.
    """
    out_path = Path(out_path)
    parent = out_path.parent

    try:
        # docs/perf/** is in-scope for creation at run time (Requirement 9 note).
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EmitError(
            f"could not create report directory {parent!s}: {exc}"
        ) from exc

    rendered = render(report)

    tmp_path: str | None = None
    try:
        # A named temp file in the destination dir keeps the eventual os.replace an
        # atomic same-filesystem rename (a cross-device rename would not be atomic).
        fd, tmp_path = tempfile.mkstemp(
            dir=str(parent),
            prefix=".posture_scores.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            # ``os.fdopen`` took ownership of ``fd``; if opening it failed the fd may
            # still be open. Closing it best-effort avoids leaking a descriptor.
            try:
                os.close(fd)
            except OSError:
                pass
            raise

        # Atomic publish. After this the temp path no longer exists.
        os.replace(tmp_path, str(out_path))
        tmp_path = None
    except OSError as exc:
        raise EmitError(
            f"failed to write report to {out_path!s}: {exc}"
        ) from exc
    finally:
        # Leave NO partial file behind on any failure path (Requirement 6.8).
        if tmp_path is not None and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
