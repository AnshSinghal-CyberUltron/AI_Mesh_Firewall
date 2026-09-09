"""Reproducibility property test for the posture scoring harness.

Additive, measurement-only (Requirement 9): this file lives under
``scripts/detection/`` and touches no production path — it imports only the pure
detection modules (``metrics``, ``thresholds``, ``corpus``, ``report``,
``attribution``) and never ``scanner.py``.

Task 10.2 implements Property 16 (deterministic reproducibility):

    For any fixed corpus and fixed posture score functions, running the
    deterministic pipeline twice produces metric values that are byte-for-byte
    identical across the two runs (excluding fields that record wall-clock
    timestamps or elapsed run duration).

**Validates: Requirements 7.2, 6.6, 10.3**

The deterministic pipeline modelled here mirrors ``score_postures._score_posture``
exactly, but with a *fixed, deterministic* ``text -> score`` map standing in for
the read-only scanner (Requirement 8.1 keeps the scanner a pure oracle, so a fixed
map is a faithful model): for a posture, ``select_threshold`` on the train
scores/labels, then ``recall`` / ``fpr`` / ``per_family_recall`` / ``per_family_fpr``
/ ``paraphrase_gap`` on the scored-set scores/labels/families at that threshold,
assembled into a :class:`report.PostureReport` and rendered to text.

There are NO wall-clock/timestamp/elapsed-duration fields anywhere in the report
models (``PostureResult`` / ``ScoredSetMeta`` / ``PostureReport``) — the report is
purely derived from the corpus + scores — so byte-identity is asserted over the
WHOLE rendered report string. Since every stage is a pure function of the corpus
and the fixed score map, the two runs must be byte-identical; this test guards
that no non-determinism (dict iteration order, banker's-rounding drift, an
accidental timestamp, or a set/float-formatting quirk) ever sneaks into the
pipeline.
"""

from __future__ import annotations

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# The detection modules import as top-level names with ``scripts/detection`` on
# the path (a self-contained additive package). Make that import work whether
# pytest is invoked from the repo root, the gateway dir, or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import attribution  # noqa: E402
import corpus  # noqa: E402
import metrics  # noqa: E402
import report  # noqa: E402
import thresholds  # noqa: E402


# ---------------------------------------------------------------------------
# Strategies (``_repro_`` prefix so this file can coexist with sibling tasks)
# ---------------------------------------------------------------------------

# A fixed posture name — the pipeline scores one posture; the report is padded to
# the mandatory three entries with two fixed "not scored" postures (a form the
# report models accept, Requirement 8.3 / Property 15) so ``build_report`` /
# ``render`` run end-to-end exactly as production does.
_REPRO_SCORED_POSTURE = "Tier1_Only"
_REPRO_PAD_POSTURES = ("Tier1_Plus_Policy", "Tier1_Plus_Semantic")

# Attack + benign family names the generated corpus may draw from. ``paraphrase``
# (a malicious family) and ``developer_traffic`` (a benign family) are always
# eligible so the paraphrase-gap and the distinguished benign family are exercised.
_repro_attack_family = st.sampled_from(
    ["paraphrase", "prompt_injection", "jailbreak", "data_leakage"]
)
_repro_benign_family = st.sampled_from(
    ["developer_traffic", "casual_chat", "code_help"]
)

# One generated corpus item, built as a real ``corpus.Corpus_Item`` (via
# ``from_obj``) so it flows through ``corpus.split_of`` / ``corpus.partition``
# exactly as a loaded corpus record does. ``text`` is drawn from a small alphabet
# so the fixed score map (below) has a good chance of assigning several items the
# SAME text (and therefore the SAME score) — exercising the distinct-score
# candidate-threshold de-duplication deterministically. Split is train / eval /
# other (an "other" split is excluded from tuning + headline and counted, R7.7).
_repro_malicious_item = st.builds(
    lambda text, family, split: corpus.Corpus_Item.from_obj(
        {"text": text, "label": "malicious", "family": family, "split": split}
    ),
    text=st.text(alphabet="abcde", min_size=0, max_size=4),
    family=_repro_attack_family,
    split=st.sampled_from(["train", "eval", "other"]),
)

_repro_benign_item = st.builds(
    lambda text, family, split: corpus.Corpus_Item.from_obj(
        {"text": text, "label": "benign", "family": family, "split": split}
    ),
    text=st.text(alphabet="abcde", min_size=0, max_size=4),
    family=_repro_benign_family,
    split=st.sampled_from(["train", "eval", "other"]),
)

# Also allow items with an unrecognized / absent label so the excluded-label
# accounting (Requirement 2.7) is part of the reproduced pipeline.
_repro_excluded_item = st.builds(
    lambda text, split: corpus.Corpus_Item.from_obj(
        {"text": text, "label": "unknown", "family": "misc", "split": split}
    ),
    text=st.text(alphabet="abcde", min_size=0, max_size=4),
    split=st.sampled_from(["train", "eval", "other"]),
)

_repro_item = st.one_of(
    _repro_malicious_item,
    _repro_benign_item,
    _repro_excluded_item,
)

# A corpus is a list of raw item dicts. Kept modest in size so ``max_examples`` of
# 100+ runs cheaply; a wide mix of labels/families/splits/texts is what stresses
# determinism, not sheer volume.
_repro_corpus = st.lists(_repro_item, min_size=0, max_size=24)

# A fixed, deterministic ``text -> score`` map for the run. Drawn ONCE per example
# and reused across BOTH pipeline runs so the "fixed posture score functions" of
# Property 16 hold. The map is keyed by the SAME small text alphabet the items use,
# so every possible item text has a defined score; a text missing from the map
# falls back to a fixed default (see ``_repro_score_map``), keeping the function
# total and deterministic.
_repro_score_dict = st.dictionaries(
    keys=st.text(alphabet="abcde", min_size=0, max_size=4),
    values=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    max_size=32,
)

_repro_scored_set_choice = st.sampled_from(["eval", "full"])


# ---------------------------------------------------------------------------
# Pure pipeline model — a faithful, side-effect-free mirror of
# ``score_postures._score_posture`` for ONE posture, using a fixed score map.
# ---------------------------------------------------------------------------


def _repro_score_map(score_dict: dict[str, float]):
    """Return a fixed, total, deterministic ``text -> score`` function.

    Models the read-only scanner as a pure oracle (Requirement 8.1): the same text
    always maps to the same score within a run. Any text absent from ``score_dict``
    maps to a fixed default (0.0), so the function is total over every possible
    item text and depends on nothing but its argument.
    """

    def score(text: str) -> float:
        return score_dict.get(text, 0.0)

    return score


def _repro_round_cells(cells):
    """Round computable per-family cells to 4 dp; pass ``NotComputable`` through.

    Mirrors ``score_postures._round_cells`` so the modelled pipeline matches the
    production reporting boundary exactly (idempotent per Property 6).
    """
    rounded = {}
    for family, cell in cells.items():
        if isinstance(cell, metrics.NotComputable):
            rounded[family] = cell
        else:
            rounded[family] = metrics.round_metric(cell)
    return rounded


def _repro_build_scored_posture_result(items, score):
    """Run the deterministic scoring pipeline for one posture and build its result.

    A faithful mirror of the scored branch of ``score_postures._score_posture``:
    select the threshold on the TRAIN split only (no eval leakage), apply it to the
    scored set, compute recall / FPR / per-family breakdowns / paraphrase gap
    rounded at the reporting boundary, and return a :class:`report.PostureResult`
    (headline ``Recall_At_Target_FPR`` form or the honest FPR-floor form).
    """
    train_items = [it for it in items if corpus.split_of(it) == corpus.TRAIN_SPLIT]
    train_scores = [score(it.text) for it in train_items]
    train_labels = [it.label for it in train_items]

    choice = thresholds.select_threshold(train_scores, train_labels)
    selected_threshold = choice.selected_threshold

    scored_scores = [score(it.text) for it in items]
    scored_labels = [it.label for it in items]
    scored_families = [it.family for it in items]

    recall_value = metrics.recall(scored_scores, scored_labels, selected_threshold)
    fpr_value = metrics.fpr(scored_scores, scored_labels, selected_threshold)

    per_family_recall = _repro_round_cells(
        metrics.per_family_recall(
            scored_scores, scored_labels, scored_families, selected_threshold
        )
    )
    per_family_fpr = _repro_round_cells(
        metrics.per_family_fpr(
            scored_scores, scored_labels, scored_families, selected_threshold
        )
    )

    family_counts: dict[str, int] = {}
    for it in items:
        if it.label not in (corpus.MALICIOUS_LABEL, corpus.BENIGN_LABEL):
            continue
        family_counts[it.family] = family_counts.get(it.family, 0) + 1

    gap = metrics.paraphrase_gap(per_family_recall)
    paraphrase_gap = gap if isinstance(gap, float) else None

    achieved_fpr = (
        metrics.round_metric(fpr_value) if isinstance(fpr_value, float) else None
    )

    if choice.target_achievable:
        recall_at_target = (
            metrics.round_metric(recall_value)
            if isinstance(recall_value, float)
            else None
        )
        return report.PostureResult(
            posture_name=_REPRO_SCORED_POSTURE,
            status=report.STATUS_SCORED,
            recall_at_target_fpr=recall_at_target,
            selected_threshold=metrics.round_metric(selected_threshold),
            achieved_fpr=achieved_fpr,
            target_fpr_achievable=True,
            per_family_recall=per_family_recall,
            per_family_fpr=per_family_fpr,
            family_counts=family_counts,
            paraphrase_gap=paraphrase_gap,
        )

    floor = thresholds.fpr_floor(train_scores, train_labels)
    fpr_floor_value = (
        metrics.round_metric(floor.fpr_floor)
        if isinstance(floor.fpr_floor, float)
        else None
    )
    recall_at_floor = metrics.round_metric(floor.recall_at_floor)
    return report.PostureResult(
        posture_name=_REPRO_SCORED_POSTURE,
        status=report.STATUS_SCORED,
        recall_at_target_fpr=None,
        selected_threshold=metrics.round_metric(selected_threshold),
        achieved_fpr=achieved_fpr,
        target_fpr_achievable=False,
        fpr_floor=fpr_floor_value,
        recall_at_floor=recall_at_floor,
        per_family_recall=per_family_recall,
        per_family_fpr=per_family_fpr,
        family_counts=family_counts,
        paraphrase_gap=paraphrase_gap,
    )


def _repro_run_pipeline(items, score_dict, which):
    """Run the full deterministic pipeline ONCE and return the rendered report text.

    Scores one posture over the chosen scored set, pads to the mandatory three
    posture entries with two fixed "not scored" postures, assembles the
    :class:`report.PostureReport` with a FIXED :class:`attribution.Attribution`
    (no timestamp / duration field exists in any model), and renders to a string.
    The returned string is the byte-for-byte artefact whose reproducibility
    Property 16 asserts.
    """
    score = _repro_score_map(score_dict)

    if which == "eval":
        scored_items = [it for it in items if corpus.split_of(it) == corpus.EVAL_SPLIT]
    else:  # "full"
        scored_items = list(items)

    scored_result = _repro_build_scored_posture_result(scored_items, score)

    # Pad to exactly three posture entries with fixed not-scored postures so the
    # report invariant (exactly three, unique names — Property 15) is satisfied and
    # ``build_report`` / ``render`` execute the full production path.
    results = [scored_result] + [
        report.PostureResult(
            posture_name=name,
            status=report.STATUS_NOT_SCORED,
            not_scored_reason="not scored in this reproducibility model",
        )
        for name in _REPRO_PAD_POSTURES
    ]

    partition = corpus.partition(scored_items)
    excluded_split = sum(
        1 for it in scored_items if corpus.split_of(it) == "other"
    )
    scored_set_meta = report.ScoredSetMeta(
        which=which,
        malicious_count=partition.malicious_count,
        benign_count=partition.benign_count,
        excluded_label_count=partition.excluded_count,
        excluded_split_count=excluded_split,
    )

    # A FIXED attribution — a fixed commit-id string, the documented reproducible
    # command, and the fixed target FPR. There is no timestamp / elapsed-duration
    # field anywhere, so nothing time-varying can enter the rendered report.
    fixed_attribution = attribution.Attribution(
        corpus_version="0" * 40,
        reproducible_command=attribution.reproducible_command(),
        target_fpr=attribution.target_fpr(),
    )

    posture_report = report.build_report(results, scored_set_meta, fixed_attribution)
    return report.render(posture_report)


# ---------------------------------------------------------------------------
# Property 16 — deterministic reproducibility of the rendered report
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(
    items=_repro_corpus,
    score_dict=_repro_score_dict,
    which=_repro_scored_set_choice,
)
def test_pipeline_is_byte_for_byte_reproducible(items, score_dict, which):
    """Running the deterministic pipeline twice yields byte-identical reports.

    **Feature: posture-scoring, Property 16.** For a fixed generated corpus and a
    fixed ``text -> score`` map, the two independent runs of the full
    threshold-selection → metrics → build_report → render pipeline produce the
    EXACT same report string. No wall-clock / timestamp / elapsed-duration field
    exists in the report models, so byte-identity is asserted over the whole
    rendered artefact.

    **Validates: Requirements 7.2, 6.6, 10.3.**
    """
    render_run1 = _repro_run_pipeline(items, score_dict, which)
    render_run2 = _repro_run_pipeline(items, score_dict, which)

    assert render_run1 == render_run2


@settings(max_examples=200)
@given(
    items=_repro_corpus,
    score_dict=_repro_score_dict,
    which=_repro_scored_set_choice,
)
def test_rendered_report_has_no_timestamp_or_duration_field(items, score_dict, which):
    """The rendered report carries no wall-clock / elapsed-duration field.

    **Feature: posture-scoring, Property 16** (the parenthetical exclusion). The
    property excludes timestamp / run-duration fields; this asserts the harness has
    NONE to exclude — the rendered report never contains a time-of-day / elapsed /
    timestamp token. Guards against a future field that would break byte-identity.

    **Validates: Requirements 7.2, 6.6, 10.3.**
    """
    rendered = _repro_run_pipeline(items, score_dict, which).lower()

    for token in ("timestamp", "elapsed", "duration", "generated at", "run at", "wall-clock", "wall clock"):
        assert token not in rendered, f"unexpected time-varying token {token!r} in report"
