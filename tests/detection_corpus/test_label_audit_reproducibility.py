"""Unit tests for label-audit sample reproducibility (task 8.2).

The label audit (`label_audit.json`) re-checks a deterministic-seed sample of at
least 10% of all Corpus_Items. Requirement 6.2 requires that repeating the
selection with the SAME seed and the SAME set of Corpus_Items produces an
IDENTICAL set of sampled items. This test pins that guarantee.

The selection method is documented in ``SAMPLING_METHODOLOGY.md`` §4 and is a pure,
stateless function of (seed, id-set):

1. Collect all ids from ``malicious.jsonl`` + ``benign.jsonl``.
2. For each id compute ``sha256((seed + id).encode("utf-8")).hexdigest()``.
3. Rank the ids ascending by that hex digest string (lexicographic).
4. Take the first ``n = max(1, ceil(fraction * total))`` ids (fraction = 0.10).

The reference ``_select_sample`` below re-implements exactly that method with the
stdlib only (no Hypothesis), so this file runs under both the gateway venv
(``gateway/.venv/bin/python -m pytest``) and a plain ``python3 -m pytest``.

Test-only and additive: nothing outside ``tests/detection_corpus/`` is read or
written, and no production code is imported.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

# The corpus (and this test) live at repo-root ``tests/detection_corpus/``.
_CORPUS_DIR = Path(__file__).resolve().parent
_AUDIT_PATH = _CORPUS_DIR / "label_audit.json"
_MALICIOUS_PATH = _CORPUS_DIR / "malicious.jsonl"
_BENIGN_PATH = _CORPUS_DIR / "benign.jsonl"

# Documented sampling rate (SAMPLING_METHODOLOGY.md §4, Requirement 6.1): at
# least 10% of all items, rounded up to the next whole item, minimum 1.
_AUDIT_FRACTION = 0.10


# --- local reference implementation of the documented selection method ---------


def _select_sample(
    ids: list[str], seed: str, fraction: float = _AUDIT_FRACTION
) -> list[str]:
    """Deterministically select the audit sample from ``ids``.

    Pure and stateless (SAMPLING_METHODOLOGY.md §4): rank the ids ascending by the
    hex ``sha256(seed + id)`` digest and take the first ``max(1, ceil(fraction *
    total))``. Ordered result — the sort is fully determined by (seed, ids), so
    two calls with the same arguments return an identical ordered list.
    """
    total = len(ids)
    n = max(1, math.ceil(fraction * total)) if total else 0
    ranked = sorted(
        ids, key=lambda i: hashlib.sha256((seed + i).encode("utf-8")).hexdigest()
    )
    return ranked[:n]


# --- fixture loaders (parse the real committed corpus) -------------------------


def _load_corpus_ids() -> list[str]:
    """Collect every Corpus_Item id from ``malicious.jsonl`` + ``benign.jsonl``."""
    ids: list[str] = []
    for path in (_MALICIOUS_PATH, _BENIGN_PATH):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            ids.append(json.loads(line)["id"])
    return ids


def _load_audit() -> dict:
    return json.loads(_AUDIT_PATH.read_text(encoding="utf-8"))


# --- tests ---------------------------------------------------------------------


def test_selection_is_deterministic_across_repeated_calls() -> None:
    """Running ``_select_sample`` twice on the same ids + seed yields an identical
    ORDERED result — the core determinism guarantee (Requirement 6.2).
    """
    ids = _load_corpus_ids()
    seed = _load_audit()["seed"]

    first = _select_sample(ids, seed)
    second = _select_sample(ids, seed)

    assert first == second, "same (ids, seed) must produce an identical ordered sample"
    # Order-independence of the resulting SET is also implied.
    assert set(first) == set(second)


def test_id_set_is_unique_and_total_matches_recorded() -> None:
    """The corpus has 748 unique ids and the audit records ``total = 748``
    (Requirement 6.3): the sample was drawn from exactly this id set.
    """
    ids = _load_corpus_ids()
    audit = _load_audit()

    assert len(ids) == len(set(ids)) == 748, "corpus ids must be unique; 748 total"
    assert audit["total"] == len(ids), "recorded total must equal the corpus id count"


def test_sample_size_is_ten_percent_ceiling() -> None:
    """The recorded sample size equals ``ceil(0.10 * total)`` = 75, and the
    reference selection produces the same count (Requirement 6.1).
    """
    ids = _load_corpus_ids()
    audit = _load_audit()

    expected_n = max(1, math.ceil(_AUDIT_FRACTION * len(ids)))
    assert expected_n == 75

    assert len(audit["sample"]) == expected_n, "recorded sample size must be ceil(10%)"
    assert len(_select_sample(ids, audit["seed"])) == expected_n


def test_stored_audit_sample_is_reproducible_from_its_seed() -> None:
    """The stored ``label_audit.json`` sample is exactly reproducible from its
    recorded seed and the corpus id set (Requirement 6.2): re-running the
    documented selection reproduces the SAME id set.
    """
    ids = _load_corpus_ids()
    audit = _load_audit()

    computed = set(_select_sample(ids, audit["seed"]))
    stored = {entry["id"] for entry in audit["sample"]}

    assert len(stored) == len(audit["sample"]), "stored sample ids must be unique"
    # Every stored id must be a real corpus id (the sample was drawn from it).
    assert stored <= set(ids), "stored sample ids must all exist in the corpus"
    assert computed == stored, (
        "recomputed sample must equal the stored audit sample "
        f"(missing={sorted(stored - computed)}, extra={sorted(computed - stored)})"
    )


def test_different_seed_yields_a_different_sample() -> None:
    """Sanity that the seed actually drives the selection (Requirement 6.2): a
    DIFFERENT seed produces a different sampled id set on this (non-tiny) corpus.
    """
    ids = _load_corpus_ids()
    seed = _load_audit()["seed"]

    baseline = set(_select_sample(ids, seed))
    other = set(_select_sample(ids, seed + "-perturbed"))

    # Guard: only meaningful when the corpus is large enough that a reseed can
    # actually change the top-n selection (n < total).
    n = max(1, math.ceil(_AUDIT_FRACTION * len(ids)))
    if n < len(ids):
        assert other != baseline, "a different seed must change the sampled id set"
