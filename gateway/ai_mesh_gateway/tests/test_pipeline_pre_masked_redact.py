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
