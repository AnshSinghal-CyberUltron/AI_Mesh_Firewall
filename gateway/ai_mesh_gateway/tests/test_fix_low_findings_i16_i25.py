"""Regression locks for the LOW/INFO findings I-16, I-19, I-20, I-25.

These are observability and contract-honesty defects rather than bypasses — but each
one made the gateway describe itself inaccurately to a client or an operator, which is
the same class of failure as the CRITICALs (an advertised control that silently does
nothing, or a mutation the caller is never told about).

  I-16  no ``x-ratelimit-*`` header on any response — both ceilings were enforced and
        NEITHER advertised, so a stock-SDK client could only discover its limit by
        tripping a 429. Reactive backoff worked; proactive pacing was impossible.
  I-19  a size/DoS rejection reported ``error.code="content_filter"`` — byte-identical
        to an unsafe-content block on the field the SDK exposes.
  I-20  ``n`` and ``max_tokens`` were silently rewritten, with no field and no header,
        while the gateway signalled every OTHER mutation via ``X-ZeroShield-*``.
  I-25  §1.7 names "human review" as an action but ``_VALID_OUTPUT_ACTIONS`` did not
        contain it, so configuring it silently fell back to the detector default.

Run: cd gateway && .venv/bin/python -m pytest \\
    ai_mesh_gateway/tests/test_fix_low_findings_i16_i25.py -q -p no:cacheprovider
"""
from __future__ import annotations

import pytest

from ai_mesh_gateway import main as gm
from ai_mesh_gateway.output_guard import (
    HUMAN_REVIEW_ACTION,
    _VALID_OUTPUT_ACTIONS,
    normalize_output_action,
)


# ── I-16 ───────────────────────────────────────────────────────────────────────
def test_i16_quota_headers_are_emitted_for_configured_limits():
    h = gm._build_ratelimit_headers(
        rate_limit_tpm=50_000, tokens_used=1_200, rpm_limit=600, current_rpm=42)
    assert h["x-ratelimit-limit-tokens"] == "50000"
    assert h["x-ratelimit-remaining-tokens"] == "48800"
    assert h["x-ratelimit-limit-requests"] == "600"
    assert h["x-ratelimit-remaining-requests"] == "558"
    assert h["x-ratelimit-reset-tokens"] and h["x-ratelimit-reset-requests"]


def test_i16_unconfigured_limits_are_not_advertised():
    """Advertising a ceiling the gateway does not enforce would be its own dishonesty —
    a client would pace against a number that means nothing."""
    assert gm._build_ratelimit_headers(rate_limit_tpm=0, rpm_limit=0) == {}
    only_tokens = gm._build_ratelimit_headers(rate_limit_tpm=1000, tokens_used=10)
    assert "x-ratelimit-limit-tokens" in only_tokens
    assert "x-ratelimit-limit-requests" not in only_tokens


def test_i16_remaining_never_goes_negative():
    h = gm._build_ratelimit_headers(rate_limit_tpm=100, tokens_used=999_999,
                                    rpm_limit=10, current_rpm=999)
    assert h["x-ratelimit-remaining-tokens"] == "0"
    assert h["x-ratelimit-remaining-requests"] == "0"


# ── I-25 ───────────────────────────────────────────────────────────────────────
def test_i25_human_review_is_a_configurable_output_action():
    """The machinery (``review_required`` -> X-ZeroShield-Review-Required) already
    existed end to end; only the ACTION NAME was missing, so an operator selecting it
    silently got the detector default instead."""
    assert HUMAN_REVIEW_ACTION in _VALID_OUTPUT_ACTIONS


def test_i25_human_review_normalises_to_flag_plus_review_required():
    """It must normalise to one of the five enforcement actions every downstream
    branch understands — the answer is still DELIVERED, but queued for a human."""
    assert normalize_output_action("human_review") == ("flag", True)


@pytest.mark.parametrize("action", ["block", "redact", "rewrite", "flag", "allow"])
def test_i25_every_other_posture_is_untouched(action):
    """No existing posture may acquire a review flag as a side effect of I-25."""
    assert normalize_output_action(action) == (action, False)


def test_i25_verdict_carries_the_review_flag():
    from ai_mesh_gateway.output_guard import OutputVerdict
    assert OutputVerdict().review_required is False


# ── I-19 ───────────────────────────────────────────────────────────────────────
def test_i19_length_verdict_carries_a_length_specific_reason_code():
    """The SIZE branch is tagged so the block path can report OpenAI's own
    ``context_length_exceeded``; the repetition heuristic is NOT tagged, so it keeps
    ``content_filter`` — the fix has to be length-specific, not a blanket rename."""
    from ai_mesh_gateway.scanner import MAX_PROMPT_LENGTH, InputScanner
    scanner = InputScanner(thread_pool_size=1)

    oversized = scanner._scan_prompt_sync("A" * (MAX_PROMPT_LENGTH + 10), False)
    assert oversized.threat_type == "dos", "oversized prompt no longer classified dos"
    assert oversized.reason_code == "context_length_exceeded", (
        f"size branch lost its length-specific reason_code: {oversized.reason_code!r}")

    repetitive = scanner._scan_prompt_sync("lorem ipsum " * 80, False)
    assert repetitive.reason_code != "context_length_exceeded", (
        "repetition is a CONTENT judgement and must not report a size error")


# ── I-20 ───────────────────────────────────────────────────────────────────────
def test_i20_clamp_header_name_is_wired():
    """The clamp list must actually reach a response header — the defect was that the
    mutation happened correctly and was never communicated."""
    import inspect
    src = inspect.getsource(gm.proxy_chat)
    assert "_request_clamps" in src, "clamp recording was removed"
    assert "X-ZeroShield-Clamped" in src, "clamp is recorded but never surfaced"
