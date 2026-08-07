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
    # Level 2 — Strict: tighten anomaly by 40%, block on any flag.
    #
    # Reachable ONLY via an explicit RAGFirewallPipeline.execute(escalation_level=…)
    # opt-in. Auto-escalation cannot get here: it needs two upstream "flag"
    # verdicts, the retriever never flags, and the ranker's only flag output
    # (ranker_stage.py, "block_on_any_flag and (anomalous_indices or
    # flagged_indices)") is itself gated on this level — circular.
    #
    # block_on_any_flag is therefore inert under auto-escalation, and stays that
    # way deliberately. Moving it down to level 1 would arm blocking for every
    # request a single upstream flag touched — the firewall taking an action the
    # operator never selected. Level 2 is opt-in, not emergent. (RAG-20)
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
