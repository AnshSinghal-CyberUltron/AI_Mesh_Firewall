"""PIPELINE-0012: already smart-masked PII must REDACT-forward, not block.

User-reported bug: a prompt carrying policy-style partial masks
(j***@a***.com, ***-**-6789, …) was blocked at input_scan as
obfuscated_pii (G53 false positive) and/or as unmaskable on the B1
redaction-no-op honesty guard.
"""
from __future__ import annotations

import os

os.environ.setdefault("ENABLE_TIER2", "false")

import pytest

import patterns
from enforcement import resolve_and_enforce
from scanner import InputScanner

_SCANNER = InputScanner()

_USER_RECORD = (
    "Please process this user record: SSN ***-**-6789, email j***@a***.com, "
    "phone ***-***-5309, credit card ****-****-****-1111."
)


def test_scanner_redacts_pre_masked_record():
    v = _SCANNER._scan_prompt_sync(_USER_RECORD, False, None)
    assert v.action == "redact"
    assert v.threat_type == "pii"
    assert v.threat_type != "obfuscated_pii"


def test_smart_mask_noop_is_expected():
    assert patterns.smart_mask_redaction_noop_is_expected(
        _USER_RECORD,
        ["email_smart_masked", "ssn_smart_masked"],
    )


def test_smart_mask_noop_expected_for_tier2_evidence_strings():
    """R2: Tier-2 matched_patterns are evidence prose, not pattern keys.

    Live Mesh blocked already-masked PII because the honesty guard required
    every key to end with ``_smart_masked``. Evidence like
    ``SSN ***-**-6789, email`` failed that check → redaction_possible=False → 400.
    """
    assert patterns.smart_mask_redaction_noop_is_expected(
        _USER_RECORD,
        ["SSN ***-**-6789, email, phone, credit card"],
    )


def test_smart_mask_noop_still_false_on_raw_pattern_keys():
    assert not patterns.smart_mask_redaction_noop_is_expected(
        _USER_RECORD,
        ["ssn", "email_smart_masked"],
    )


def test_resolve_and_enforce_redact_when_noop_expected():
    decision = resolve_and_enforce(
        scanner_recommendation="redact",
        scanner_action="redact",
        scanner_threat_type="pii",
        scanner_confidence=0.85,
        scanner_tier="tier_1",
        scanner_matched_patterns=["email_smart_masked"],
        enforcement_mode="block",
        redaction_possible=True,
    )
    assert decision.action == "redact"
    assert not decision.is_terminal_block


def test_resolve_and_enforce_blocks_when_noop_and_not_smart_masked():
    decision = resolve_and_enforce(
        scanner_recommendation="redact",
        scanner_action="redact",
        scanner_threat_type="pii",
        scanner_confidence=0.85,
        enforcement_mode="block",
        redaction_possible=False,
    )
    assert decision.action == "block"
    assert decision.is_terminal_block


def test_output_redact_redaction_possible_smart_mask_noop():
    from main import _output_redact_redaction_possible

    text = "Contact j***@a***.com for help."
    assert _output_redact_redaction_possible(
        response_text=text,
        sanitized_text=text,
        scan_text=text,
        matched_patterns=["email_smart_masked"],
    )


@pytest.mark.asyncio
async def test_output_guard_takes_no_action_on_smart_masked_only():
    """Already-safe smart masks are NOT PII: NO action at all, bytes unchanged.

    Regression 1: neutralize_markdown_split_pii treated j***@a***.com as
    markdown-split PII → [PII_REDACTED] under output_pii_action=redact.

    Regression 2 (2026-07-19): this previously asserted action == "flag". That is
    WRONG — the operator is the sole owner of their org's actions, and emitting a
    flag here shows an action they never selected (an org configured "rewrite"
    would see "flag"). Already-masked output contains no raw PII, so it is not a
    finding: the guard must return the EMPTY verdict and the configured action
    applies ONLY to genuinely raw PII. See the frozen contract in
    output_guard._check_pii_secrets and test_output_guard_category_routing.py.
    """
    from output_guard import OutputGuard, sanitize_output_for_verdict

    text = (
        'Example:\n```json\n{\n  "ssn": "***-**-6789",\n'
        '  "email": "j***@a***.com",\n'
        '  "phone": "***-***-5309",\n'
        '  "credit_card": "****-****-****-1111"\n}\n```\n'
    )
    guard = OutputGuard(
        scanner=_SCANNER,
        config={"pii_detection_enabled": True, "output_pii_action": "redact"},
    )
    v = await guard.inspect(text)
    # "allow" is the empty/no-finding verdict — the guard took NO action.
    assert v.action == "allow"
    assert not v.matched_patterns
    out = sanitize_output_for_verdict(text, v, redact_pii_fn=patterns.redact_all)
    assert out == text
    assert "[PII_REDACTED]" not in out
    assert "j***@a***.com" in out


def test_output_redact_redaction_possible_empty_content_smart_mask_only():
    from main import _output_redact_redaction_possible

    assert _output_redact_redaction_possible(
        response_text="",
        sanitized_text="",
        scan_text="email j***@a***.com",
        matched_patterns=["email_smart_masked"],
    )


def test_output_redact_redaction_possible_real_change():
    from main import _output_redact_redaction_possible

    assert _output_redact_redaction_possible(
        response_text="alice@corp.example",
        sanitized_text="a***@c***.example",
        matched_patterns=["email"],
    )


def test_output_redact_redaction_possible_reasoning_channel_smart_mask():
    """PIPELINE-0030: smart-mask in reasoning_content must not fail-closed when content is benign."""
    from main import _output_redact_redaction_possible

    reasoning = (
        "The user asked to process: SSN ***-**-6789, email j***@a***.com, "
        "phone ***-***-5309, credit card ****-****-****-1111."
    )
    content = "Could you clarify what processing you would like me to perform?"
    scan_text = f"{reasoning}\n{content}"
    assert _output_redact_redaction_possible(
        response_text=content,
        sanitized_text=content,
        scan_text=scan_text,
        matched_patterns=["email_smart_masked"],
    )


def test_maybe_promote_reasoning_to_content():
    from main import _maybe_promote_reasoning_to_content, _extract_response_from_completion

    completion = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "The answer is 42.",
                }
            }
        ]
    }
    assert _maybe_promote_reasoning_to_content(completion) is True
    assert _extract_response_from_completion(completion) == "The answer is 42."


def test_enforce_output_allows_output_smart_mask_noop():
    from enforcement import enforce_output
    from main import _output_redact_redaction_possible

    text = _USER_RECORD
    possible = _output_redact_redaction_possible(
        response_text=text,
        sanitized_text=text,
        scan_text=text,
        matched_patterns=["email_smart_masked"],
    )
    decision = enforce_output(
        verdict_action="redact",
        verdict_threat_type="pii",
        enforcement_mode="block",
        redaction_possible=possible,
    )
    assert decision.action == "redact"
    assert not decision.is_terminal_block


def test_strip_internal_completion_keys_json_serializable():
    import json

    from main import _strip_internal_completion_keys
    from output_guard import OutputVerdict

    completion = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "_pipeline_output_scan_verdict": OutputVerdict(
            action="allow",
            threat_type="pii",
            matched_patterns=["email_smart_masked"],
        ),
    }
    _strip_internal_completion_keys(completion)
    json.dumps(completion)
    assert "_pipeline_output_scan_verdict" not in completion


def test_resolve_pipeline_blocked_by_output_blocked_code():
    from main import _resolve_pipeline_blocked_by

    assert (
        _resolve_pipeline_blocked_by(
            code="output_blocked",
            threat_category="pii",
            detection_tier="",
        )
        == "output_guardrail"
    )
