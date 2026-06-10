"""Guard Model UI copy when PII policy redacts but model recommends block."""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_mesh_gateway.pipeline_trace import build_guard_fields, enrich_zeroshield_from_verdict


@dataclass
class _PiiVerdict:
    tier: str = "tier_2"
    action: str = "block"
    threat_type: str = "pii"
    confidence: float = 0.99
    detail: str = "Bedrock ML detected threat: SSN ***-**-6789, email j***@a***.com"
    reason_code: str = "model_recommendation"
    matched_patterns: list[str] = field(
        default_factory=lambda: ["SSN ***-**-6789", "email j***@a***.com"]
    )
    scan_meta: dict = field(
        default_factory=lambda: {
            "recommended_action": "block",
            "findings": [{"evidence": "SSN ***-**-6789", "category": "pii"}],
        }
    )


def test_build_guard_fields_redact_overrides_model_block_recommendation():
    guard = build_guard_fields(
        verdict=_PiiVerdict(),
        stage_action="allow",
        final_action="redact",
        tier="tier_2",
        zs={"detection_tier": "tier_2", "threat_type": "pii", "confidence": 0.99},
    )

    assert guard["guard_action"] == "redact"
    assert "enforcement: REDACT" in guard["guard_reason"]
    assert "Model recommendation: BLOCK" in guard["guard_reason"]
    assert "Org PII policy redacted sensitive fields" in guard["guard_reason"]
    assert guard["recommended_action"] == "block"


def test_enrich_zeroshield_carries_pii_redact_policy_note():
    base = {
        "action": "allow",
        "detection_tier": "tier_2",
        "threat_type": "pii",
        "confidence": 0.99,
    }
    enriched = enrich_zeroshield_from_verdict(
        base,
        scan_verdict=_PiiVerdict(),
        final_action="redact",
    )

    assert enriched["guard_action"] == "redact"
    assert "audit only" in enriched.get("guard_reason", "")
