# Feature: observability-contract
# Validates: Requirements 6.5, 14.3
"""Out-of-vocabulary coverage (task 7.3) — an unknown reason/code surfaces an error, never a merge.

Example/unit tests: an out-of-vocab rejection ``reason`` or an unknown Posture_Code must NOT fall
back to a merged/default series. Instead the per-reason / per-code counters are preserved untouched
and a dedicated vocab-error / coverage-gap indicator is incremented (R6.5 / R7.1 / R14.3), and the
loud ``raise_on_unknown`` surface raises ``VocabularyError`` when asked.
"""

from __future__ import annotations

import pytest

from gateway_v2.edge.counters import (
    COVERAGE_GAP_SERIES,
    POSTURE_CODES,
    REASON_VOCAB,
    VOCAB_ERROR_SERIES,
    PostureCounters,
    RejectionCounters,
    VocabularyError,
)


def test_out_of_vocab_reason_increments_indicator_no_merge() -> None:
    counters = RejectionCounters()
    before = counters.series()
    counters.observe(503, "not-a-real-reason")

    after = counters.series()
    # The vocab-error indicator advanced by exactly one.
    assert after[VOCAB_ERROR_SERIES] == before[VOCAB_ERROR_SERIES] + 1
    # No per-reason counter moved; no merged/default series was created.
    for reason in REASON_VOCAB:
        name = f'amf_rejected{{status="503",reason="{reason}"}}'
        assert after[name] == before[name], f"{reason} bucket moved on an out-of-vocab reason"
    assert "amf_rejected_503" not in after, "a merged rejected_503 series was created"


def test_out_of_vocab_posture_increments_coverage_gap_no_merge() -> None:
    counters = PostureCounters()
    before = counters.series()
    counters.observe("not-a-real-posture")

    after = counters.series()
    assert after[COVERAGE_GAP_SERIES] == before[COVERAGE_GAP_SERIES] + 1
    for code in POSTURE_CODES:
        name = f"amf_posture_refusals_{code}_total"
        assert after[name] == before[name], f"{code} counter moved on an unknown code"


def test_raise_on_unknown_surfaces_vocabulary_error() -> None:
    with pytest.raises(VocabularyError):
        RejectionCounters().observe(503, "bogus", raise_on_unknown=True)
    with pytest.raises(VocabularyError):
        PostureCounters().observe("bogus", raise_on_unknown=True)
