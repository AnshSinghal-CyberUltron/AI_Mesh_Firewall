"""Tier-2 clean pass metadata enrichment for operator UI."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ai_mesh_gateway.pipeline_trace import enrich_zeroshield_from_verdict


@dataclass
class _FakeVerdict:
    tier: str = "tier_2"
    action: str = "allow"
    threat_type: str = "clean"
    confidence: float = 0.99
    detail: str = "ZeroShield Guard Model (Tier-2) completed — no threats detected."
    reason_code: str = "tier2_pass"
    matched_patterns: list[str] = field(default_factory=list)
    scan_meta: dict = field(default_factory=lambda: {"llm_guard_score": 0.01})


def test_enrich_zeroshield_tier2_clean_pass():
    base = {
        "action": "allow",
        "detection_tier": "tier_2",
        "threat_type": "clean",
        "confidence": 0.99,
        "detail": "ZeroShield Guard Model (Tier-2) completed — no threats detected.",
    }
    verdict = _FakeVerdict()
    enriched = enrich_zeroshield_from_verdict(base, scan_verdict=verdict, final_action="allow")

    assert enriched["scan_outcome"] == "clean"
    assert enriched["risk_score"] == 0.01
    assert enriched["reason_code"] == "tier2_pass"
    assert enriched.get("guard_reason")
    assert enriched.get("guard_model")
