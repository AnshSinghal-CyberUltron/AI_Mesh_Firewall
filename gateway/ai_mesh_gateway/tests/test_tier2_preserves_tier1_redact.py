"""Regression: Tier-2 may ESCALATE a deterministic Tier-1 redact to a block, but
must NEVER DOWNGRADE it. A real-fleet wire capture caught raw PII (SSN/email/phone/
card) egressing to a third-party provider because ``scan_prompt_with_tier2`` only
returned early on ``tier1.action == "block"`` — a Tier-1 ``redact`` (PII/secret)
fell through and the combined verdict was rebuilt entirely from Tier-2, so a
degraded (parse-failed/truncated) OR over-permissive ("allow"/"monitor") Tier-2
silently stripped the deterministic redaction and the gateway forwarded raw.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import InputScanner  # noqa: E402

PII_PROMPT = (
    "my ssn is 234-56-7891, email a@b.com, call me at 8005559042, "
    "card 4111111111111111 — summarize my account"
)


def _degraded():
    """Tier-2 parse-failure / truncation (the captured leak's exact shape)."""
    return {
        "meta": {"recommended_action": "monitor", "parse_failed": True, "raw_findings": []},
        "llm_guard": {"score": 0.5, "is_valid": True, "degraded": True},
    }


def _allow():
    """Tier-2 LLM guard missed the PII and recommends allow."""
    return {
        "meta": {"recommended_action": "allow", "raw_findings": []},
        "llm_guard": {"score": 0.0, "degraded": False, "is_valid": True},
    }


def _block():
    """Tier-2 found a real injection in the same prompt -> escalate."""
    return {
        "meta": {
            "recommended_action": "block",
            "raw_findings": [
                {"category": "prompt_injection", "confidence": 0.95, "evidence": "ignore instructions"}
            ],
        },
        "llm_guard": {"score": 0.95, "degraded": False, "is_valid": True},
    }


class _DummyBedrock:
    model = "zeroshield-guard"


@pytest.fixture
def scanner(monkeypatch):
    s = InputScanner()
    s.tier2_enabled = True
    s._bedrock_scanner = _DummyBedrock()
    s._tier2_sample_rate = 1.0
    s._tier2_cache_ttl = 0.0  # deterministic per-call mock (no verdict cache)
    return s


@pytest.mark.asyncio
async def test_degraded_tier2_preserves_tier1_pii_redact(scanner, monkeypatch):
    monkeypatch.setattr(scanner, "_bedrock_scan_sync", lambda *a, **k: _degraded())
    v = await scanner.scan_prompt_with_tier2(PII_PROMPT, org_tier2_strict=False)
    assert v.action == "redact", f"degraded Tier-2 DOWNGRADED Tier-1 PII redact -> {v.action}"
    assert v.threat_type == "pii"


@pytest.mark.asyncio
async def test_permissive_tier2_allow_preserves_tier1_pii_redact(scanner, monkeypatch):
    monkeypatch.setattr(scanner, "_bedrock_scan_sync", lambda *a, **k: _allow())
    v = await scanner.scan_prompt_with_tier2(PII_PROMPT, org_tier2_strict=False)
    assert v.action == "redact", f"Tier-2 'allow' DOWNGRADED Tier-1 PII redact -> {v.action}"


@pytest.mark.asyncio
async def test_tier2_block_still_escalates_pii_prompt(scanner, monkeypatch):
    monkeypatch.setattr(scanner, "_bedrock_scan_sync", lambda *a, **k: _block())
    v = await scanner.scan_prompt_with_tier2(PII_PROMPT, org_tier2_strict=False)
    assert v.action == "block", f"Tier-2 block escalation on a PII prompt was lost -> {v.action}"


@pytest.mark.asyncio
async def test_clean_prompt_degraded_tier2_behaviour_unchanged(scanner, monkeypatch):
    """No Tier-1 redact to preserve -> the degraded fail-open path is untouched."""
    monkeypatch.setattr(scanner, "_bedrock_scan_sync", lambda *a, **k: _degraded())
    v = await scanner.scan_prompt_with_tier2("what is the weather today", org_tier2_strict=False)
    assert v.action in ("flag", "allow")
    assert v.threat_type != "pii"
