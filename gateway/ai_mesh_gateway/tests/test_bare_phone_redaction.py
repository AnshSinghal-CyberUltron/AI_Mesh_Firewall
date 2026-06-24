"""Regression: Tier-2 can flag bare 10-digit phones that phone_us regex skips."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patterns import detect_pii, redact_all, redact_evidence_digit_spans
from scanner import InputScanner, ScanVerdict


def test_bare_phone_redacted_with_contextual_pattern():
    text = "my phone number is 8929554991"
    assert "phone_us_bare_contextual" in detect_pii(text)
    assert "8929554991" not in redact_all(text)
    assert "***-***-4991" in redact_all(text)


def test_bare_phone_without_context_not_false_positive():
    text = "order id 8929554991 shipped"
    assert "phone_us_bare_contextual" not in detect_pii(text)
    assert "8929554991" in redact_all(text)


def test_redact_pii_uses_tier2_evidence_spans():
    scanner = InputScanner()
    verdict = ScanVerdict(
        action="flag",
        threat_type="pii",
        matched_patterns=["8929554991"],
        scan_meta={"findings": [{"category": "pii", "evidence": "8929554991"}]},
    )
    out = scanner.redact_pii("call me at 8929554991 anytime", verdict=verdict)
    assert "8929554991" not in out
    assert "***-***-4991" in out
