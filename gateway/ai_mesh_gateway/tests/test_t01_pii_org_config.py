"""T01 L01-3: honor org pii_detection_enabled; do not hardcode True in proxy_chat."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scanner import InputScanner, strip_disabled_pii_verdict, ScanVerdict
from main import (
    _org_pii_detection_enabled,
    _scanner_kwargs_honoring_pii_toggle,
)
from enforcement import resolve_and_enforce
from patterns import detect_secrets


def test_missing_key_keeps_platform_floor_on():
    assert _org_pii_detection_enabled({}) is True
    assert _org_pii_detection_enabled(None) is True
    assert _org_pii_detection_enabled({"enforcement_mode": "block"}) is True


def test_explicit_false_disables_floor():
    assert _org_pii_detection_enabled({"pii_detection_enabled": False}) is False
    assert _org_pii_detection_enabled({"pii_detection_enabled": "false"}) is False
    assert _org_pii_detection_enabled({"pii_detection_enabled": 0}) is False


def test_explicit_true_enables_floor():
    assert _org_pii_detection_enabled({"pii_detection_enabled": True}) is True
    assert _org_pii_detection_enabled({"pii_detection_enabled": "true"}) is True


def test_proxy_chat_does_not_hardcode_pii_true():
    src = Path(__file__).resolve().parents[1] / "main.py"
    text = src.read_text(encoding="utf-8")
    assert "_pii_detection_enabled = True" not in text
    assert "_org_pii_detection_enabled(" in text
    assert "_scanner_kwargs_honoring_pii_toggle(" in text


def test_pii_off_drops_pii_scanner_kwargs_keeps_secret():
    pii = SimpleNamespace(
        threat_type="pii",
        action="redact",
        confidence=0.9,
        tier="tier1",
        matched_patterns=["ssn"],
        detail="ssn",
    )
    dropped = _scanner_kwargs_honoring_pii_toggle(
        pii, recommendation="redact", pii_detection_enabled=False
    )
    assert dropped["scanner_threat_type"] is None

    secret = SimpleNamespace(
        threat_type="secret",
        action="redact",
        confidence=0.99,
        tier="tier1",
        matched_patterns=["aws"],
        detail="aws",
    )
    kept = _scanner_kwargs_honoring_pii_toggle(
        secret, recommendation="redact", pii_detection_enabled=False
    )
    assert kept["scanner_threat_type"] == "secret"


def test_maskable_pii_allows_when_floor_off():
    decision = resolve_and_enforce(
        scanner_recommendation="redact",
        scanner_threat_type="pii",
        scanner_confidence=0.95,
        org_policy_action=None,
        enforcement_mode="block",
        redaction_possible=True,
        pii_detection_enabled=False,
    )
    assert decision.action == "allow"
    assert not decision.is_terminal_block
    assert not decision.is_redact


def test_akia_is_secret_even_when_pii_off(monkeypatch):
    monkeypatch.setenv("ENABLE_TIER2", "false")
    sc = InputScanner(thread_pool_size=1, config={})
    try:
        assert detect_secrets("key AKIAIOSFODNN7EXAMPLE here")
        off = sc._scan_prompt_sync(
            "Please use key AKIAIOSFODNN7EXAMPLE",
            False,
            pii_detection_enabled=False,
        )
        assert off.threat_type == "secret"
        assert off.action == "redact"
        on = sc._scan_prompt_sync(
            "Please use key AKIAIOSFODNN7EXAMPLE",
            False,
            pii_detection_enabled=True,
        )
        assert on.threat_type == "secret"
    finally:
        sc._executor.shutdown(wait=False)
        sc._bedrock_executor.shutdown(wait=False)


def test_ssn_bypasses_when_pii_off(monkeypatch):
    monkeypatch.setenv("ENABLE_TIER2", "false")
    sc = InputScanner(thread_pool_size=1, config={})
    try:
        off = sc._scan_prompt_sync(
            "Please say hello. Reference number 123-45-6789.",
            False,
            pii_detection_enabled=False,
        )
        assert off.action == "allow"
        assert (off.threat_type or "none") in ("", "none")
        on = sc._scan_prompt_sync(
            "Please say hello. Reference number 123-45-6789.",
            False,
            pii_detection_enabled=True,
        )
        assert on.threat_type == "pii"
        assert on.action == "redact"
    finally:
        sc._executor.shutdown(wait=False)
        sc._bedrock_executor.shutdown(wait=False)


def test_proxy_chat_passes_pii_toggle_into_scanner():
    src = Path(__file__).resolve().parents[1] / "main.py"
    text = src.read_text(encoding="utf-8")
    assert "pii_detection_enabled=_pii_detection_enabled" in text


def test_strip_disabled_pii_drops_pii_keeps_secret_and_injection():
    pii = ScanVerdict(action="redact", threat_type="pii")
    assert strip_disabled_pii_verdict(pii).threat_type in ("", "none")
    assert strip_disabled_pii_verdict(pii).action == "allow"
    secret = ScanVerdict(action="redact", threat_type="secret")
    assert strip_disabled_pii_verdict(secret).threat_type == "secret"
    inj = ScanVerdict(action="block", threat_type="prompt_injection")
    assert strip_disabled_pii_verdict(inj).action == "block"
    t2 = ScanVerdict(action="flag", threat_type="sensitive_content")
    assert strip_disabled_pii_verdict(t2).action == "allow"
