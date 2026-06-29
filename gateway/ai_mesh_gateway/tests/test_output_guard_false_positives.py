"""Regression tests for output-guard false positives (safety refusals, PII labels)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ai_mesh_gateway.output_guard import (
    OutputGuard,
    OutputVerdict,
    sanitize_output_for_verdict,
)
from ai_mesh_gateway.patterns import detect_pii, detect_secrets, is_safety_refusal_output


AADHAAR_REFUSAL = (
    "I appreciate you being careful with that information, but I want to give you "
    "some important security advice:\n\n"
    "**Please don't share your Aadhaar number (even partially) in any chat or online "
    "conversation**, including with me. Here's why:\n\n"
    "1. **I'm not a secure system** - I'm a public AI service\n"
    "2. **No verification needed** - I have no way to verify who's asking"
)

PII_LABEL_REFUSAL = (
    "However, I should clarify my approach: **I don't process, store, or retain "
    "personal information from conversations**. This includes:\n\n"
    "- Social Security Numbers\n"
    "- Email addresses\n"
    "- Phone numbers\n"
    "- Credit card numbers"
)

PII_ECHO = (
    "Processing record: SSN 123-45-6789, email john.smith@acmecomp.com, "
    "phone 555-867-5309, credit card 4111-1111-1111-1111."
)


class _AllowVerdict:
    action = "allow"
    threat_type = ""
    confidence = 0.0
    detail = ""
    matched_patterns: list = []
    matched_values: dict = {}


class _PiiVerdict:
    def __init__(self, matched: dict[str, str], threat_type: str = "pii"):
        self.action = "flag"
        self.threat_type = threat_type
        self.confidence = 0.85
        self.detail = f"PII detected in output: {', '.join(matched.keys())}"
        self.matched_patterns = list(matched.keys())
        self.matched_values = dict(matched)


class _StubScanner:
    def __init__(self, matched: dict[str, str] | None = None, threat_type: str = "pii"):
        self._matched = matched
        self._threat_type = threat_type

    async def scan_output(self, text: str):
        if self._matched:
            return _PiiVerdict(self._matched, threat_type=self._threat_type)
        return _AllowVerdict()


@pytest.fixture()
def guard() -> OutputGuard:
    return OutputGuard(scanner=_StubScanner(), config={"hallucination_flag_enabled": True})


@pytest.mark.asyncio
async def test_aadhaar_safety_refusal_not_hallucination(guard: OutputGuard) -> None:
    assert is_safety_refusal_output(AADHAAR_REFUSAL)
    score = await guard.score_hallucination(AADHAAR_REFUSAL, context_chunks=[])
    assert score.risk_score == 0.0
    verdict = await guard.inspect(AADHAAR_REFUSAL, context_chunks=[])
    assert verdict.action == "allow"


@pytest.mark.asyncio
async def test_pii_category_labels_only_not_detected() -> None:
    assert detect_pii(PII_LABEL_REFUSAL) == {}
    guard = OutputGuard(scanner=_StubScanner(), config={})
    verdict = await guard.inspect(PII_LABEL_REFUSAL, context_chunks=[])
    assert verdict.action == "allow"


@pytest.mark.asyncio
async def test_pii_echo_detected_with_matched_values(guard: OutputGuard) -> None:
    matched = detect_pii(PII_ECHO)
    assert "ssn" in matched
    assert "email" in matched
    scanner = _StubScanner(matched=matched)
    og = OutputGuard(scanner=scanner, config={})
    verdict = await og.inspect(PII_ECHO, context_chunks=[])
    assert verdict.action == "redact"
    assert verdict.threat_type == "pii"
    # matched_values deliberately keeps RAW values (operator-console telemetry).
    assert verdict.matched_values.get("ssn") == "123-45-6789"
    assert verdict.matched_values.get("email") == "john.smith@acmecomp.com"
    # detail is client-facing: raw values must NEVER appear there.
    assert "123-45-6789" not in verdict.detail
    assert "john.smith@acmecomp.com" not in verdict.detail
    assert "4111-1111-1111-1111" not in verdict.detail
    assert "555-867-5309" not in verdict.detail
    # ...but the masked forms (and pattern keys) do.
    assert "ssn" in verdict.detail
    assert "***-**-6789" in verdict.detail
    assert "j***@a***.com" in verdict.detail


@pytest.mark.asyncio
async def test_secret_echo_detail_masks_raw_value() -> None:
    secret_echo = 'Connecting with password = "hunter2secret" as configured.'
    matched = detect_secrets(secret_echo)
    assert "password_assignment" in matched
    scanner = _StubScanner(matched=matched, threat_type="secret")
    # Disable the credential-exposure detector (it also fires on password
    # assignments and its block action would win) to isolate the secrets path.
    og = OutputGuard(scanner=scanner, config={"output_credential_enabled": False})
    verdict = await og.inspect(secret_echo, context_chunks=[])
    assert verdict.action == "redact"
    assert verdict.threat_type == "secret"
    # Raw secret stays in matched_values (operator telemetry)...
    assert "hunter2secret" in verdict.matched_values.get("password_assignment", "")
    # ...but never in the client-facing detail string.
    assert "hunter2secret" not in verdict.detail
    assert 'password = "***' in verdict.detail


@pytest.mark.asyncio
async def test_detail_fallback_mask_when_value_not_rematchable() -> None:
    # A matched value that does NOT re-match any pattern out of context must
    # still be masked in detail via the deterministic partial-mask fallback.
    matched = {"custom_token": "hunter2plainvalue"}
    scanner = _StubScanner(matched=matched, threat_type="secret")
    og = OutputGuard(scanner=scanner, config={})
    verdict = await og.inspect("custom_token leak hunter2plainvalue", context_chunks=[])
    assert verdict.action == "redact"
    assert verdict.matched_values.get("custom_token") == "hunter2plainvalue"
    assert "hunter2plainvalue" not in verdict.detail
    assert "hu***" in verdict.detail


@pytest.mark.asyncio
async def test_hallucination_redact_uses_rewrite_not_pii_mask(guard: OutputGuard) -> None:
    fabricated = (
        "According to Dr. Smith et al., studies show roughly 87% of users "
        "prefer this product. Research indicates this is absolutely certain."
    )
    score = await guard.score_hallucination(fabricated, context_chunks=["unrelated context"])
    assert score.risk_score >= 0.2

    verdict = OutputVerdict(
        action="redact",
        threat_type="hallucination",
        confidence=0.5,
        detail="hallucination_risk=0.500",
    )
    original = AADHAAR_REFUSAL
    sanitized = sanitize_output_for_verdict(original, verdict)
    assert sanitized != original
    assert "cannot verify" in sanitized.lower()
