"""PII prompts with Tier-2 redact recommendation must redact-and-allow (not fail-closed block)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_mesh_gateway.main import (
    _apply_input_pii_redaction,
    _is_redactable_pii_threat,
    _prompt_has_raw_sensitive_data,
    _scrub_input_pii_for_egress,
)
from ai_mesh_gateway.scanner import InputScanner, ScanVerdict


PII_PROMPT = (
    "[user]: Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, "
    "phone 555-867-5309, credit card 4111-1111-1111-1111."
)
MASKED_PROMPT = (
    "[user]: Please process this user record: SSN ***-**-6789, email j***@a***.com, "
    "phone ***-***-5309, credit card ****-****-****-1111."
)


@pytest.fixture
def scanner(monkeypatch):
    sc = InputScanner(thread_pool_size=2, config={"tier2_enabled": False})
    import ai_mesh_gateway.main as main_mod

    monkeypatch.setattr(main_mod, "INPUT_SCANNER", sc)
    return sc


def test_is_redactable_pii_threat_accepts_pii_detection_category():
    assert _is_redactable_pii_threat("PII_DETECTION") is True


def test_apply_input_pii_redaction_masks_sensitive_data_leakage_prompt(scanner):
    verdict = ScanVerdict(
        action="flag",
        threat_type="pii",
        confidence=0.95,
        detail="Tier-2 suggested redaction",
        matched_patterns=["SSN ***-**-6789"],
        tier="tier_2",
    )
    verdict.scan_meta = {
        "recommended_action": "redact",
        "findings": [{"evidence": "SSN ***-**-6789", "category": "pii"}],
    }
    redacted = _apply_input_pii_redaction(PII_PROMPT, verdict)
    assert redacted != PII_PROMPT
    assert "123-45-6789" not in redacted
    assert "john.smith@acmecomp.com" not in redacted
    assert "4111-1111-1111-1111" not in redacted


def test_scrub_input_pii_for_egress_clears_raw_sensitive_data(scanner):
    verdict = ScanVerdict(
        action="flag",
        threat_type="pii",
        confidence=0.95,
        detail="Tier-2 suggested redaction",
        matched_patterns=["SSN ***-**-6789"],
        tier="tier_2",
    )
    scrubbed = _scrub_input_pii_for_egress(PII_PROMPT, verdict)
    assert not _prompt_has_raw_sensitive_data(scrubbed)


def test_prompt_has_raw_sensitive_data_false_on_already_masked_prompt():
    assert not _prompt_has_raw_sensitive_data(MASKED_PROMPT)
