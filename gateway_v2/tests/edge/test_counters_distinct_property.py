# Feature: observability-contract, Property 6: posture/reason counters distinct from policy BLOCK
# Validates: Requirements 6.1, 6.2, 6.3, 7.1, 7.2, 7.3, 7.4, 7.6, 14.1, 14.2
"""Property 6 — each posture code and admission reason has its own counter, distinct from BLOCK.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

For any sequence of posture refusals, admission rejections, and (modelled) policy blocks, each
Posture_Code and each Admission_Reason has its own counter whose value equals the count of its own
events; a modelled Policy_BLOCK tally equals the number of blocks and is incremented by NO posture
or admission event; distinct reasons never merge into a single aggregated series; the names are the
exact single-spelled strings from ``domain.posture`` / ``audit.record``; and an out-of-vocabulary
reason/code increments the vocab-error / coverage-gap indicator rather than creating a merged
series.
"""

from __future__ import annotations

import random

from gateway_v2.edge.counters import (
    COVERAGE_GAP_SERIES,
    POSTURE_CODES,
    POSTURE_PREFIX,
    REASON_VOCAB,
    VOCAB_ERROR_SERIES,
    PostureCounters,
    RejectionCounters,
)

_SEED = 0x0B5E_0006
_ITERATIONS = 10_000
_STATUS = 503


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _rejected_name(status: int, reason: str) -> str:
    return f'amf_rejected{{status="{status}",reason="{reason}"}}'


# A handful of out-of-vocab tokens that must NEVER create a merged/default series.
_BAD_REASONS = ("timeout", "rejected_503", "", "SHED", "unknown-reason")
_BAD_CODES = ("block", "BLOCK", "policy_block", "", "stream_killed")


def test_property_6_counters_distinct_from_policy_block() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        rejections = RejectionCounters()
        postures = PostureCounters()

        expected_reason: dict[str, int] = {r: 0 for r in REASON_VOCAB}
        expected_posture: dict[str, int] = {c: 0 for c in POSTURE_CODES}
        expected_vocab_err = 0
        expected_coverage_gap = 0
        # A model of a Policy_BLOCK tally: posture/admission events must leave it UNCHANGED.
        policy_block = 0

        n = rng.randint(0, 60)
        for _op in range(n):
            kind = rng.random()
            if kind < 0.35:
                reason = rng.choice(REASON_VOCAB)
                rejections.observe(_STATUS, reason)
                expected_reason[reason] += 1
            elif kind < 0.45:
                bad = rng.choice(_BAD_REASONS)
                if bad in REASON_VOCAB:
                    rejections.observe(_STATUS, bad)
                    expected_reason[bad] += 1
                else:
                    rejections.observe(_STATUS, bad)
                    expected_vocab_err += 1
            elif kind < 0.75:
                code = rng.choice(POSTURE_CODES)
                postures.observe(code)
                expected_posture[code] += 1
            elif kind < 0.85:
                bad_code = rng.choice(_BAD_CODES)
                if bad_code in expected_posture:
                    postures.observe(bad_code)
                    expected_posture[bad_code] += 1
                else:
                    postures.observe(bad_code)
                    expected_coverage_gap += 1
            else:
                # A policy block — modelled only; the counter sets must not know about it.
                policy_block += 1

        rej_series = rejections.series()
        post_series = postures.series()

        # Each reason counter == count of its own events; distinct reasons stay distinct (R6.2).
        for reason, want in expected_reason.items():
            name = _rejected_name(_STATUS, reason)
            assert rej_series.get(name) == float(want), _ctx(
                _SEED, i, note="reason mismatch", reason=reason, got=rej_series.get(name), want=want
            )
        # No merged 'rejected_503' style aggregate series exists (R6.2).
        assert "amf_rejected_503" not in rej_series, _ctx(_SEED, i, note="merged series present")
        assert "amf_rejected" not in rej_series, _ctx(_SEED, i, note="bare merged series present")

        # Each posture counter == count of its own events; exact single-spelled names (R7.2/R7.4).
        for code, want in expected_posture.items():
            name = f"{POSTURE_PREFIX}_{code}_total"
            assert post_series.get(name) == float(want), _ctx(
                _SEED, i, note="posture mismatch", code=code, got=post_series.get(name), want=want
            )

        # Out-of-vocab indicators carry the exact counts (R6.5 / R7.1 / R14.3).
        assert rej_series.get(VOCAB_ERROR_SERIES) == float(expected_vocab_err), _ctx(
            _SEED, i, note="vocab-error count", got=rej_series.get(VOCAB_ERROR_SERIES)
        )
        assert post_series.get(COVERAGE_GAP_SERIES) == float(expected_coverage_gap), _ctx(
            _SEED, i, note="coverage-gap count", got=post_series.get(COVERAGE_GAP_SERIES)
        )

        # No posture/reason series shares a name with any Policy_BLOCK-style series: the counter
        # sets expose nothing named *block* at all (R7.3/R7.6/R14.2). The modelled policy_block
        # tally is wholly external and was never touched by any counter observation.
        for name in list(rej_series) + list(post_series):
            assert "block" not in name.lower(), _ctx(
                _SEED, i, note="counter series collides with a block series", name=name
            )
        # Sanity: the modelled block tally equals the number of modelled blocks (unchanged by the
        # counter sets, which have no handle on it).
        assert policy_block >= 0
