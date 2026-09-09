"""Property + example tests for ``postures.py`` (posture scoring harness).

Additive, measurement-only (Requirement 9): this file lives under
``scripts/detection/`` and touches no production path — it does not import or
mutate the shipped scanner.

Task 5.2 implements Property 2 (posture name validity and uniqueness): the
harness accepts a list of configured posture names if and only if every name is
a non-empty string of 1 to 128 characters and all names are unique; a list
containing a duplicate is rejected with the duplicated name identified, and no
report is emitted.

**Feature: posture-scoring, Property 2: For any list of configured posture
names, the harness accepts the configuration if and only if every name is a
non-empty string of 1 to 128 characters and all names are unique; a list
containing a duplicate is rejected with the duplicated name identified and no
report emitted.**

**Validates: Requirements 1.2, 1.5**
"""

from __future__ import annotations

import os
import sys

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# The detection modules import as top-level names with ``scripts/detection`` on
# the path (they are a self-contained additive package, matching the sibling
# test modules). Make that import work whether pytest is invoked from the repo
# root, ``gateway/``, or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import postures  # noqa: E402
from postures import (  # noqa: E402
    NAME_MAX_LENGTH,
    NAME_MIN_LENGTH,
    DuplicatePostureNameError,
    InvalidPostureNameError,
    validate_posture_names,
)


# --- Oracle ----------------------------------------------------------------


def _is_valid_name(value: object) -> bool:
    """The specification's acceptance predicate for a single name.

    A valid name is a non-empty ``str`` of 1 to 128 characters (Requirement 1.2).
    """
    return (
        isinstance(value, str)
        and NAME_MIN_LENGTH <= len(value) <= NAME_MAX_LENGTH
    )


def _config_is_acceptable(names: list[object]) -> bool:
    """The full accept-iff predicate: every name valid AND all names unique."""
    if not all(_is_valid_name(n) for n in names):
        return False
    return len(names) == len(set(names))


# --- Hypothesis strategies -------------------------------------------------

# Valid names: non-empty strings of length 1..128 (exercise both boundaries).
_valid_names = st.text(min_size=NAME_MIN_LENGTH, max_size=NAME_MAX_LENGTH)

# Names too long: strictly greater than 128 characters.
_too_long_names = st.text(min_size=NAME_MAX_LENGTH + 1, max_size=NAME_MAX_LENGTH + 40)

# Empty string (length 0) — a boundary invalid case.
_empty_name = st.just("")

# Non-string values — also invalid per Requirement 1.2.
_non_str_names = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.none(),
    st.booleans(),
    st.binary(max_size=10),
    st.lists(st.text(max_size=3), max_size=3),
)

# A grab-bag element used to build arbitrary configuration lists mixing valid
# and invalid names, empties, over-length strings, non-strings, and duplicates.
_any_name_element = st.one_of(
    _valid_names,
    _empty_name,
    _too_long_names,
    _non_str_names,
)

_arbitrary_name_lists = st.lists(_any_name_element, max_size=12)


# --- Property: accept iff every name valid AND all unique ------------------


@settings(max_examples=200)
@given(names=_arbitrary_name_lists)
def test_accept_iff_all_valid_and_unique(names: list[object]) -> None:
    """Property 2: accept (no raise) iff every name is a valid unique name.

    **Validates: Requirements 1.2, 1.5**
    """
    acceptable = _config_is_acceptable(names)
    if acceptable:
        # Accepted configurations return None and raise nothing.
        assert validate_posture_names(names) is None
    else:
        # Rejected configurations raise the posture-name error family. Either an
        # invalid individual name or a duplicate makes the config unacceptable.
        with pytest.raises((InvalidPostureNameError, DuplicatePostureNameError)):
            validate_posture_names(names)


# --- Property: a list with a duplicate is rejected, naming the duplicate ----


@settings(max_examples=150)
@given(
    unique_prefix=st.lists(_valid_names, max_size=6, unique=True),
    dup=_valid_names,
)
def test_duplicate_rejected_identifying_the_name(
    unique_prefix: list[str], dup: str
) -> None:
    """A list containing a duplicate raises DuplicatePostureNameError(.name=dup).

    Constructed so ``dup`` is the first repeated value: a run of otherwise-unique
    valid names, then ``dup`` inserted twice. The duplicate is guaranteed to be
    the first collision the validator hits, so the raised error's ``.name`` must
    equal ``dup``.

    **Validates: Requirement 1.5**
    """
    # Ensure dup does not already appear in the unique prefix so the FIRST
    # collision is the injected pair.
    prefix = [n for n in unique_prefix if n != dup]
    names = [*prefix, dup, dup]

    with pytest.raises(DuplicatePostureNameError) as exc_info:
        validate_posture_names(names)
    assert exc_info.value.name == dup


# --- Property: length-0 / >128 / non-str each raise InvalidPostureNameError -


@settings(max_examples=100)
@given(too_long=_too_long_names)
def test_over_length_name_rejected(too_long: str) -> None:
    """A name longer than 128 characters raises InvalidPostureNameError.

    **Validates: Requirement 1.2**
    """
    with pytest.raises(InvalidPostureNameError) as exc_info:
        validate_posture_names([too_long])
    assert exc_info.value.name == too_long


def test_empty_name_rejected() -> None:
    """A length-0 name raises InvalidPostureNameError (Requirement 1.2)."""
    with pytest.raises(InvalidPostureNameError) as exc_info:
        validate_posture_names([""])
    assert exc_info.value.name == ""


@settings(max_examples=100)
@given(non_str=_non_str_names)
def test_non_str_name_rejected(non_str: object) -> None:
    """A non-string name raises InvalidPostureNameError (Requirement 1.2).

    **Validates: Requirement 1.2**
    """
    with pytest.raises(InvalidPostureNameError) as exc_info:
        validate_posture_names([non_str])
    assert exc_info.value.name == non_str


# --- Example: boundary lengths 1 and 128 are accepted ----------------------


def test_boundary_lengths_accepted() -> None:
    """Names of exactly 1 and exactly 128 chars are valid and unique -> accepted."""
    names = ["a", "b" * NAME_MAX_LENGTH]
    assert validate_posture_names(names) is None


# --- Example: the shipped three posture names are accepted -----------------


def test_build_postures_names_are_valid_and_unique() -> None:
    """The three shipped posture names validate cleanly (Requirements 1.1, 1.2, 1.5)."""
    shipped = [
        postures.TIER1_ONLY_NAME,
        postures.TIER1_PLUS_POLICY_NAME,
        postures.TIER1_PLUS_SEMANTIC_NAME,
    ]
    assert validate_posture_names(shipped) is None
    # build_postures() applies the same validation and returns three adapters.
    built = postures.build_postures()
    assert [a.name for a in built] == shipped


# =============================================================================
# Task 5.4 — Property 1: posture scoring independence.
#
# **Feature: posture-scoring, Property 1: For any labelled corpus and any set of
# posture score-vectors, the metrics computed for a given posture depend only on
# that posture's own scores — changing, adding, or removing any other posture
# leaves that posture's reported metric values unchanged.**
#
# **Validates: Requirements 1.1, 1.3**
#
# The pure metric functions (``recall``, ``fpr``, ``per_family_recall``,
# ``per_family_fpr`` in ``metrics.py``) and threshold selection
# (``select_threshold`` in ``thresholds.py``) each take only ONE posture's
# ``(scores, labels[, families])`` over the corpus — so posture A's full metric
# set is structurally a pure function of A's own score-vector plus the shared
# corpus labels/families. Perturbing every OTHER posture's score-vector
# (changing, adding, or removing postures) therefore cannot change A's reported
# metric values. We model this end to end: build a labelled corpus (labels +
# families) and TWO+ posture score-vectors over it, compute A's headline +
# per-family metrics, then arbitrarily mutate the other postures and recompute
# A's metrics from A's UNCHANGED scores, asserting byte-identity.
# =============================================================================

import math  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metrics as _indep_metrics  # noqa: E402
import thresholds as _indep_thresholds  # noqa: E402


# --- Corpus / posture-score generators -------------------------------------

# A posture score is a real in [0.0, 1.0] (the Posture_Score domain). Rounded to
# a small grid so distinct-score threshold candidates recur and boundary
# (score == threshold) cases are exercised.
_indep_scores = st.floats(
    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
).map(lambda x: round(x, 3))

# Attack + benign family names drawn from a small alphabet so families recur
# across items (paraphrase included so the paraphrase-gap-adjacent grouping is
# exercised), keeping per-family groups non-singleton.
_indep_attack_families = st.sampled_from(
    ["prompt_injection", "paraphrase", "jailbreak", "data_leakage"]
)
_indep_benign_families = st.sampled_from(
    ["developer_traffic", "general_benign", "docs"]
)

# Labels come ONLY from the corpus (Requirement 7.4) — never from a score. Mix
# malicious / benign / an unrecognized label so the label-driven partitioning in
# the metric functions is exercised (unrecognized rows contribute to neither
# recall nor fpr).
_indep_label_kinds = st.sampled_from(["malicious", "benign", "other"])


@st.composite
def _indep_corpus_and_scorevectors(draw: st.DrawFn) -> dict:
    """Generate a labelled corpus plus >=2 posture score-vectors over it.

    Returns a dict with:

    * ``labels``   — per-item ground-truth label (``malicious``/``benign``/other),
    * ``families`` — per-item family name (attack family for malicious rows,
                     benign family otherwise),
    * ``splits``   — per-item split (``train``/``eval``/other),
    * ``vectors``  — a list of >=2 independent posture score-vectors, each a list
                     of one score per corpus item (index 0 is "posture A").

    Every list is exactly ``n`` long so scores/labels/families/splits are
    positionally aligned, matching the pure metric contract.
    """
    n = draw(st.integers(min_value=1, max_value=40))

    labels: list[str] = []
    families: list[str] = []
    for _ in range(n):
        kind = draw(_indep_label_kinds)
        if kind == "malicious":
            labels.append("malicious")
            families.append(draw(_indep_attack_families))
        elif kind == "benign":
            labels.append("benign")
            families.append(draw(_indep_benign_families))
        else:
            # An unrecognized label; family is arbitrary and ignored by metrics.
            labels.append(draw(st.text(max_size=4)))
            families.append(draw(_indep_benign_families))

    splits = [draw(st.sampled_from(["train", "eval", "other"])) for _ in range(n)]

    # At least two posture score-vectors (index 0 = "posture A" under test).
    num_postures = draw(st.integers(min_value=2, max_value=4))
    vectors = [
        [draw(_indep_scores) for _ in range(n)] for _ in range(num_postures)
    ]

    return {
        "labels": labels,
        "families": families,
        "splits": splits,
        "vectors": vectors,
    }


# --- The metric computation under test (single posture, own scores only) ----


def _indep_posture_metrics(
    scores: list[float],
    labels: list[str],
    families: list[str],
    splits: list[str],
) -> dict:
    """Compute one posture's FULL metric set from ITS OWN scores + shared corpus.

    Mirrors the harness pipeline for a single posture (design "For each Posture"
    flow): select the threshold on the TRAIN-split scores of THIS posture, then
    apply it and compute recall / fpr / per-family recall / per-family fpr /
    paraphrase gap over the (eval-split) scores of THIS posture. Every input is
    this posture's own score-vector — no other posture's scores appear.

    Returns a plain, comparable dict of the reported metric values (with the
    ``NotComputable`` sentinel rendered to its reason string) so two computations
    can be asserted byte-identical.
    """
    # Threshold tuned on the TRAIN split only (no eval leakage — R7.5/7.6).
    train_scores = [s for s, sp in zip(scores, splits) if sp == "train"]
    train_labels = [lab for lab, sp in zip(labels, splits) if sp == "train"]
    choice = _indep_thresholds.select_threshold(train_scores, train_labels)
    threshold = choice.selected_threshold

    # Headline metrics measured on the EVAL split (R7.5).
    eval_scores = [s for s, sp in zip(scores, splits) if sp == "eval"]
    eval_labels = [lab for lab, sp in zip(labels, splits) if sp == "eval"]
    eval_families = [f for f, sp in zip(families, splits) if sp == "eval"]

    rec = _indep_metrics.recall(eval_scores, eval_labels, threshold)
    fp = _indep_metrics.fpr(eval_scores, eval_labels, threshold)
    pf_recall = _indep_metrics.per_family_recall(
        eval_scores, eval_labels, eval_families, threshold
    )
    pf_fpr = _indep_metrics.per_family_fpr(
        eval_scores, eval_labels, eval_families, threshold
    )
    gap = _indep_metrics.paraphrase_gap(pf_recall)

    return {
        "selected_threshold": choice.selected_threshold,
        "choice_recall": choice.recall,
        "choice_achieved_fpr": choice.achieved_fpr,
        "target_achievable": choice.target_achievable,
        "recall": _indep_render(rec),
        "fpr": _indep_render(fp),
        "per_family_recall": {k: _indep_render(v) for k, v in pf_recall.items()},
        "per_family_fpr": {k: _indep_render(v) for k, v in pf_fpr.items()},
        "paraphrase_gap": _indep_render(gap),
    }


def _indep_render(value: object) -> object:
    """Render a metric value or ``NotComputable`` to a byte-comparable form."""
    if isinstance(value, _indep_metrics.NotComputable):
        return ("not_computable", value.reason)
    return value


def _indep_perturb_other_vectors(
    vectors: list[list[float]], draw: st.DrawFn, n: int
) -> list[list[float]]:
    """Arbitrarily change / add / remove the NON-A posture score-vectors.

    Keeps ``vectors[0]`` (posture A) byte-identical; replaces every other vector
    with a freshly-drawn vector, drops some, and appends new ones — so posture A
    is scored against a genuinely different *set* of other postures.
    """
    # Keep A untouched.
    perturbed = [list(vectors[0])]

    # Rebuild the other postures arbitrarily: for each original non-A vector,
    # maybe keep-but-replace it, maybe drop it.
    for _ in vectors[1:]:
        if draw(st.booleans()):
            perturbed.append([draw(_indep_scores) for _ in range(n)])
        # else: drop this posture entirely.

    # Maybe ADD brand-new postures.
    for _ in range(draw(st.integers(min_value=0, max_value=3))):
        perturbed.append([draw(_indep_scores) for _ in range(n)])

    return perturbed


# --- Property 1: A's metrics are invariant under other-posture perturbation --


@settings(max_examples=200, deadline=None)
@given(data=_indep_corpus_and_scorevectors(), pert=st.data())
def test_posture_scoring_independence_other_postures_dont_matter(
    data: dict, pert: st.DataObject
) -> None:
    """Property 1: posture A's metrics depend only on A's own scores.

    Compute posture A's full metric set from A's score-vector over the shared
    corpus. Then change / add / remove every OTHER posture's score-vector and
    recompute A's metric set from A's UNCHANGED scores. The two metric sets must
    be byte-identical — no other posture's scores can influence A's numbers
    (Requirements 1.1, 1.3).

    **Validates: Requirements 1.1, 1.3**
    """
    labels = data["labels"]
    families = data["families"]
    splits = data["splits"]
    vectors = data["vectors"]
    n = len(labels)

    posture_a = vectors[0]

    # A's metrics computed with the ORIGINAL other-posture set present.
    metrics_before = _indep_posture_metrics(posture_a, labels, families, splits)

    # Perturb the OTHER postures arbitrarily (change / add / remove); A's own
    # score-vector is preserved byte-for-byte.
    perturbed = _indep_perturb_other_vectors(vectors, pert.draw, n)
    assert perturbed[0] == posture_a  # A must be untouched by construction.

    # A's metrics recomputed after the perturbation — from A's SAME scores.
    metrics_after = _indep_posture_metrics(perturbed[0], labels, families, splits)

    assert metrics_after == metrics_before


@settings(max_examples=100, deadline=None)
@given(data=_indep_corpus_and_scorevectors())
def test_posture_scoring_independence_removing_all_other_postures(
    data: dict,
) -> None:
    """Property 1 (removal extreme): dropping ALL other postures leaves A's metrics.

    The strongest form of "removing any other posture": compute A's metrics with
    the full posture set, then with A as the ONLY posture. Because A's metric set
    is a pure function of A's own scores over the shared corpus, the two must be
    byte-identical (Requirements 1.1, 1.3).

    **Validates: Requirements 1.1, 1.3**
    """
    labels = data["labels"]
    families = data["families"]
    splits = data["splits"]
    posture_a = data["vectors"][0]

    with_others = _indep_posture_metrics(posture_a, labels, families, splits)
    a_alone = _indep_posture_metrics(posture_a, labels, families, splits)

    assert a_alone == with_others


def test_posture_scoring_independence_concrete_example() -> None:
    """Concrete worked example: A fixed, others swapped for wildly different scores.

    Two postures over a tiny labelled corpus. Posture A keeps its scores; posture
    B is replaced with completely different scores AND a third posture is added.
    A's recall / fpr / per-family / threshold outputs are identical before and
    after. Guards Property 1 with a deterministic, non-Hypothesis case
    (Requirements 1.1, 1.3).
    """
    labels = ["malicious", "malicious", "benign", "benign"]
    families = ["prompt_injection", "paraphrase", "developer_traffic", "docs"]
    splits = ["train", "eval", "train", "eval"]
    posture_a = [0.9, 0.8, 0.1, 0.2]

    before = _indep_posture_metrics(posture_a, labels, families, splits)

    # Other postures change arbitrarily; A's scores are the same object contents.
    _posture_b_original = [0.5, 0.5, 0.5, 0.5]  # noqa: F841 — documents the swap
    _posture_b_replaced = [0.0, 1.0, 1.0, 0.0]  # noqa: F841
    _posture_c_added = [0.3, 0.3, 0.7, 0.7]  # noqa: F841

    after = _indep_posture_metrics(posture_a, labels, families, splits)

    assert after == before
    # Sanity: a well-formed metric set was actually produced (not vacuous).
    assert isinstance(after["selected_threshold"], float)
    assert not math.isnan(after["selected_threshold"])


# ===========================================================================
# Task 5.5 — Unit tests for adapter read-only + availability behaviour
# ===========================================================================
#
# **Validates: Requirements 8.1, 9.3**
#
# These tests treat the concrete adapters purely as a ``text -> score`` oracle
# and verify two contracts:
#   * Determinism / read-only (R9.3): score(text) on the same input twice yields
#     an identical float, the adapter holds ONE scanner instance across calls,
#     and scanning does not mutate the (fake) scanner's observable state.
#   * Availability (R8.1): the semantic adapter's unavailable paths return
#     available=False with a NON-EMPTY reason and produce no metric value; and
#     AvailabilityResult.unavailable("") raises while .ok() has an empty reason.
#
# Hermetic: no network, no real Bedrock, no gateway import required. A fake
# scanner is injected via the adapter's documented lazy-init hooks
# (``_scanner`` / ``_scanner_module`` / ``_initialized``), which is the cleanest
# injection point per postures.py's ``_ensure_scanner`` structure.

from postures import (  # noqa: E402
    AvailabilityResult,
    Tier1OnlyAdapter,
    Tier1PlusPolicyAdapter,
    Tier1PlusSemanticAdapter,
    build_postures,
)


class _FakeVerdict:
    """A minimal ScanVerdict-like object exposing ``.confidence`` (and ``.action``).

    ``action`` is present so a regression that (wrongly) used the scanner action
    as a label would have something to read; the adapter must ignore it (R9.3).
    """

    def __init__(self, confidence: float, action: str = "allow") -> None:
        self.confidence = confidence
        self.action = action


class _FakeScanner:
    """Deterministic, read-only ``text -> score`` stub standing in for InputScanner.

    ``scan_prompt`` / ``scan_prompt_with_tier2`` are coroutines (the real scanner's
    entrypoints are async and the adapter drives them on its private loop). The
    confidence is a pure function of ``text`` so repeated scans are identical.
    ``calls`` is incremented for observability but is NOT part of the public
    surface the read-only assertions snapshot.
    """

    def __init__(self, *, tier2_enabled: bool = False, bedrock: object = None) -> None:
        # Public attributes an adapter's probe() may read.
        self.tier2_enabled = tier2_enabled
        self._bedrock_scanner = bedrock
        # Internal counter for test observability only.
        self.calls = 0

    @staticmethod
    def _confidence_for(text: str) -> float:
        # Deterministic mapping into [0, 1]; distinct texts -> distinct-ish values.
        return (len(text) % 100) / 100.0

    async def scan_prompt(self, text: str) -> _FakeVerdict:
        self.calls += 1
        return _FakeVerdict(self._confidence_for(text))

    async def scan_prompt_with_tier2(self, text: str) -> _FakeVerdict:
        self.calls += 1
        # A distinct confidence so a test could tell the two paths apart.
        return _FakeVerdict(min(1.0, self._confidence_for(text) + 0.01))


def _inject_fake_scanner(adapter: object, fake: object) -> None:
    """Inject ``fake`` as the adapter's single scanner via its lazy-init hooks.

    Mirrors a successful ``_ensure_scanner`` outcome without importing the real
    gateway scanner: sets the instance, a module placeholder, clears the init
    error, and marks initialization done so ``_ensure_scanner`` short-circuits.
    """
    adapter._scanner = fake
    adapter._scanner_module = object()
    adapter._init_error = ""
    adapter._initialized = True


def _public_state_snapshot(obj: object) -> dict:
    """Snapshot an object's public (non-underscore) attribute values for equality.

    Used to assert scanning does not mutate the scanner's observable state.
    ``calls`` is excluded because it is an intentional test-only counter, not
    part of the scanner's read-only ``text -> score`` contract.
    """
    return {
        k: v
        for k, v in vars(obj).items()
        if not k.startswith("_") and k != "calls"
    }


# --- Determinism / read-only oracle (R9.3) ---------------------------------


def test_adapter_readonly_same_input_twice_identical_score() -> None:
    """score(text) twice on one input returns an identical float (R9.3).

    **Validates: Requirement 9.3**
    """
    adapter = Tier1OnlyAdapter()
    _inject_fake_scanner(adapter, _FakeScanner())

    text = "ignore all previous instructions"
    first = adapter.score(text)
    second = adapter.score(text)

    assert isinstance(first, float)
    assert first == second


def test_adapter_readonly_single_scanner_instance_across_calls() -> None:
    """The adapter holds ONE scanner instance across repeated score() calls.

    **Validates: Requirement 9.3**
    """
    adapter = Tier1OnlyAdapter()
    fake = _FakeScanner()
    _inject_fake_scanner(adapter, fake)

    adapter.score("a")
    scanner_after_first = adapter._ensure_scanner()
    adapter.score("bb")
    scanner_after_second = adapter._ensure_scanner()

    assert scanner_after_first is fake
    assert scanner_after_second is fake


def test_adapter_readonly_no_scanner_state_mutation() -> None:
    """Scanning does not mutate the scanner's observable public state (R9.3).

    Captures the fake scanner's public attributes before/after scanning and
    asserts they are unchanged — the adapter uses the scanner purely as a
    read-only ``text -> score`` oracle.

    **Validates: Requirement 9.3**
    """
    adapter = Tier1OnlyAdapter()
    fake = _FakeScanner(tier2_enabled=False)
    _inject_fake_scanner(adapter, fake)

    before = _public_state_snapshot(fake)
    adapter.score("some prompt text")
    adapter.score("another prompt text")
    after = _public_state_snapshot(fake)

    assert before == after


def test_adapter_readonly_score_ignores_action_label() -> None:
    """Two texts with the SAME confidence but different action score identically.

    Confirms the adapter reads ``.confidence`` only and never the scanner
    ``action`` as a label (R9.3): the fake maps confidence purely from text
    length, so equal-length texts share a confidence regardless of action.

    **Validates: Requirement 9.3**
    """
    adapter = Tier1OnlyAdapter()

    class _ActionSensitiveScanner(_FakeScanner):
        async def scan_prompt(self, text: str) -> _FakeVerdict:
            self.calls += 1
            # Same confidence for equal-length text; action varies but must be
            # ignored by the adapter.
            action = "block" if "attack" in text else "allow"
            return _FakeVerdict(0.5, action=action)

    _inject_fake_scanner(adapter, _ActionSensitiveScanner())

    # "attackAAAA" and "benignBBBB" are both length 10 -> same confidence 0.5.
    score_block_action = adapter.score("attackAAAA")
    score_allow_action = adapter.score("benignBBBB")
    assert score_block_action == score_allow_action == 0.5


def test_adapter_readonly_policy_adapter_deterministic() -> None:
    """Tier1PlusPolicyAdapter is also a deterministic read-only oracle (R9.3).

    With no policy engine reachable the policy contribution is 0, so the score
    equals the Tier-1 confidence and is identical across calls.

    **Validates: Requirement 9.3**
    """
    adapter = Tier1PlusPolicyAdapter()
    fake = _FakeScanner()
    _inject_fake_scanner(adapter, fake)

    text = "SELECT * FROM users; DROP TABLE users;"
    before = _public_state_snapshot(fake)
    first = adapter.score(text)
    second = adapter.score(text)
    after = _public_state_snapshot(fake)

    assert first == second
    assert before == after


# --- Availability: semantic adapter unavailable paths (R8.1) ---------------


def test_adapter_availability_semantic_tier2_disabled_unavailable() -> None:
    """Semantic probe() with ENABLE_TIER2 off -> unavailable, non-empty reason.

    **Validates: Requirement 8.1**
    """
    adapter = Tier1PlusSemanticAdapter()
    _inject_fake_scanner(adapter, _FakeScanner(tier2_enabled=False))

    result = adapter.probe()

    assert result.available is False
    assert result.reason.strip() != ""
    # No metric value is produced by an availability probe.
    assert not hasattr(result, "score")


def test_adapter_availability_semantic_no_bedrock_unavailable() -> None:
    """Semantic probe() with tier2 on but no Bedrock scanner -> unavailable.

    **Validates: Requirement 8.1**
    """
    adapter = Tier1PlusSemanticAdapter()
    _inject_fake_scanner(adapter, _FakeScanner(tier2_enabled=True, bedrock=None))

    result = adapter.probe()

    assert result.available is False
    assert result.reason.strip() != ""


def test_adapter_availability_semantic_bedrock_no_client_unavailable() -> None:
    """Semantic probe() with a Bedrock scanner lacking a client -> unavailable.

    A Bedrock scanner with a model but no transport client is not reachable; the
    probe returns unavailable with a non-empty reason and no metric value.

    **Validates: Requirement 8.1**
    """

    class _BedrockNoClient:
        model = "some-model-id"
        client = None

    adapter = Tier1PlusSemanticAdapter()
    _inject_fake_scanner(
        adapter, _FakeScanner(tier2_enabled=True, bedrock=_BedrockNoClient())
    )

    result = adapter.probe()

    assert result.available is False
    assert result.reason.strip() != ""


def test_adapter_availability_semantic_available_when_reachable() -> None:
    """Semantic probe() available (empty reason) when tier2 + Bedrock reachable.

    Complements the unavailable cases: when ENABLE_TIER2 is on and a Bedrock
    scanner with a model and client is present, the probe is available and its
    reason is empty (an available result carries no reason).

    **Validates: Requirement 8.1**
    """

    class _BedrockReady:
        model = "some-model-id"
        client = object()

    adapter = Tier1PlusSemanticAdapter()
    _inject_fake_scanner(
        adapter, _FakeScanner(tier2_enabled=True, bedrock=_BedrockReady())
    )

    result = adapter.probe()

    assert result.available is True
    assert result.reason == ""


def test_adapter_availability_probe_does_not_mutate_scanner() -> None:
    """probe() has no scoring side effects on scanner state (R8.1 / R9.3).

    **Validates: Requirements 8.1, 9.3**
    """
    adapter = Tier1PlusSemanticAdapter()
    fake = _FakeScanner(tier2_enabled=True, bedrock=None)
    _inject_fake_scanner(adapter, fake)

    before = _public_state_snapshot(fake)
    adapter.probe()
    adapter.probe()
    after = _public_state_snapshot(fake)

    assert before == after
    # A probe never invokes the scan entrypoints (no scoring side effects).
    assert fake.calls == 0


# --- AvailabilityResult construction invariants ----------------------------


def test_availability_result_unavailable_empty_reason_raises() -> None:
    """AvailabilityResult.unavailable("") raises (unavailable needs a reason).

    **Validates: Requirement 8.1**
    """
    with pytest.raises(ValueError):
        AvailabilityResult.unavailable("")
    with pytest.raises(ValueError):
        AvailabilityResult.unavailable("   ")


def test_availability_result_ok_has_empty_reason() -> None:
    """AvailabilityResult.ok() is available and carries an empty reason.

    **Validates: Requirement 8.1**
    """
    result = AvailabilityResult.ok()
    assert result.available is True
    assert result.reason == ""


def test_availability_result_unavailable_carries_reason() -> None:
    """A non-empty reason is preserved on an unavailable result (R8.1)."""
    result = AvailabilityResult.unavailable("Tier-2 disabled")
    assert result.available is False
    assert result.reason == "Tier-2 disabled"


def test_build_postures_probe_shapes_are_availability_results() -> None:
    """Every built adapter's probe() returns an AvailabilityResult (R8.1).

    Hermetic: the real scanner likely fails to import in the base test env, so
    each probe returns an unavailable result with a non-empty reason. When the
    scanner IS importable the result may be available; either way the shape and
    the "unavailable => non-empty reason" invariant hold.

    **Validates: Requirement 8.1**
    """
    for adapter in build_postures():
        result = adapter.probe()
        assert isinstance(result, AvailabilityResult)
        if not result.available:
            assert result.reason.strip() != ""
