"""Property test for the SDK-retry client stratum (B6 / R2-21), task 4.9.

Covers the pure ``load_harness.strata.retry`` module only (no I/O, no event loop):

* **Property 9 — retry honors ``x-should-retry: false`` and reports amplification**: for any
  configured base/aggressive shares and any sequence of shed responses,
  (a) ``should_reissue(response, client_is_retrying=...)`` returns ``False`` whenever
  ``response.should_retry is False`` — a ``x-should-retry: false`` shed is NEVER re-issued, even by
  a retrying client; and (b) the reported total/original offered ratio from ``amplification_ratio``
  / ``RetryOutcome.amplification`` equals the realized amplification ``total_offered / original``
  (``1.0`` when ``original == 0``).

House idiom: seeded ``random.Random`` loops of >= 10,000 total iterations with the seed logged in
the assertion message; NO ``hypothesis``. The stratum is pure, so no injected clock is needed. Test
files are not under the import-linter layer contract.
"""

from __future__ import annotations

import random

from load_harness.strata.retry import (
    RetryOutcome,
    RetryPolicy,
    ShedResponse,
    amplification_ratio,
    client_retries,
    retry_outcome,
    should_reissue,
)

_ITERATIONS = 10_000

# Feature: load-harness-r2-21, Property 9: retry honors x-should-retry false
# and reports amplification
# Validates: Requirements 5.1, 5.2, 5.4


def test_property9_retry_honors_should_retry_false_and_reports_amplification() -> None:
    """Non-retryable sheds are never re-issued, and amplification == total/original (Property 9)."""
    seed = 0x5_09
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        # Random configured shares (allowed to exceed [0,1]; combined_share clamps).
        policy = RetryPolicy(
            base_share=rng.uniform(-0.25, 1.25),
            aggressive_share=rng.uniform(-0.25, 1.25),
        )
        combined = policy.combined_share
        assert 0.0 <= combined <= 1.0, f"combined_share out of range (seed={seed:#x}, i={i})"

        # A random sequence of shed responses, each a retryable / non-retryable mix.
        shed_sequence = [
            ShedResponse(should_retry=rng.random() < 0.5)
            for _ in range(rng.randint(0, 12))
        ]
        for response in shed_sequence:
            # The client-retry draw is independent of the should_retry contract.
            retrying = client_retries(policy, rng)

            reissued = should_reissue(response, client_is_retrying=retrying)

            # (a) x-should-retry: false is NEVER re-issued, even by a retrying client (Req 5.2).
            if response.should_retry is False:
                assert reissued is False, (
                    "non-retryable shed re-issued "
                    f"(seed={seed:#x}, i={i}, retrying={retrying})"
                )
            # A re-issue implies BOTH a retrying client AND a retryable response (Req 5.1).
            if reissued:
                assert retrying and response.should_retry, (
                    f"re-issue without both conditions (seed={seed:#x}, i={i})"
                )
            # Full characterization: reissue iff retrying AND retryable.
            assert reissued == (retrying and response.should_retry), (
                f"should_reissue != (retrying and should_retry) (seed={seed:#x}, i={i})"
            )

        # (b) Amplification equals the realized total/original ratio (Req 5.4).
        original = rng.randint(0, 5_000)
        reissues = rng.randint(0, 5_000)
        outcome = retry_outcome(original_requests=original, reissued_requests=reissues)

        assert isinstance(outcome, RetryOutcome)
        assert outcome.original_requests == original, f"original clobbered (seed={seed:#x}, i={i})"
        assert outcome.reissued_requests == reissues, f"reissues clobbered (seed={seed:#x}, i={i})"

        total = outcome.total_offered
        assert total == original + reissues, f"total_offered wrong (seed={seed:#x}, i={i})"

        expected = 1.0 if original == 0 else total / original
        assert outcome.amplification == expected, (
            f"RetryOutcome.amplification != total/original (seed={seed:#x}, i={i})"
        )
        assert (
            amplification_ratio(original_requests=original, total_offered=total) == expected
        ), f"amplification_ratio != total/original (seed={seed:#x}, i={i})"

        # Amplification is >= 1.0 whenever there is any original load (re-issues only add).
        if original > 0:
            assert outcome.amplification >= 1.0, (
                f"amplification below 1.0 with positive original (seed={seed:#x}, i={i})"
            )
