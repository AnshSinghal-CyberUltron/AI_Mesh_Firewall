"""Inter-stage enforcement escalation for the RAG firewall pipeline."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EscalationConfig:
    """Threshold modifiers applied at a given escalation level."""

    anomaly_threshold_multiplier: float = 1.0  # lower = stricter
    trust_score_minimum: float = 0.0  # docs below this are dropped
    force_context_scan: bool = False
    force_sensitive_scan: bool = False
    block_on_any_flag: bool = False
    relevance_threshold_boost: float = 0.0  # added to base threshold


ESCALATION_LEVELS: dict[int, EscalationConfig] = {
    # Level 0 — Normal: default thresholds
    0: EscalationConfig(),
    # Level 1 — Elevated: tighten anomaly by 20%, force scans
    1: EscalationConfig(
        anomaly_threshold_multiplier=0.80,
        trust_score_minimum=0.3,
        force_context_scan=True,
        force_sensitive_scan=True,
        relevance_threshold_boost=0.05,
    ),
    # Level 2 — Strict: tighten anomaly by 40%, block on any flag
    2: EscalationConfig(
        anomaly_threshold_multiplier=0.60,
        trust_score_minimum=0.5,
        force_context_scan=True,
        force_sensitive_scan=True,
        block_on_any_flag=True,
        relevance_threshold_boost=0.10,
    ),
}


def get_escalation_config(level: int) -> EscalationConfig:
    """Return the escalation config for the given level, clamped to [0,2]."""
    return ESCALATION_LEVELS.get(min(max(level, 0), 2), ESCALATION_LEVELS[0])
