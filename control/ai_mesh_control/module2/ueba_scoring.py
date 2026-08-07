"""Pure UEBA v2 scoring functions (learning, active deviation, graduation)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from module2.ueba_metrics import hourly_counts_chronological, latest_hourly_count

THREAT_SEVERITY_WEIGHTS: dict[str, float] = {
    "prompt_injection": 1.0,
    "jailbreak": 1.0,
    "injection": 0.95,
    "malware": 0.9,
    "data_exfiltration": 0.85,
    "data_leakage": 0.8,
    "sensitive_data": 0.75,
    "pii": 0.5,
    "toxicity": 0.6,
    "content_blocked": 0.55,
    "tier2_degraded": 0.4,
    "kill_switch": 1.0,
    "model_not_allowed": 0.9,
    "model_isolation": 0.85,
    "high_risk_actor": 0.95,
    "unknown": 0.2,
    "none": 0.0,
}

MIN_BASELINE_SAMPLES = 20
MIN_CURRENT_EVENTS = 20
LOW_BASELINE_EPS = 0.01
LLM_BLEND_WEIGHT = 0.45

# Fallback when org settings / key override are absent.
DEFAULT_GRADUATION_REQUESTS = 50


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def sigmoid(x: float) -> float:
    if x <= -20:
        return 0.0
    if x >= 20:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def threat_severity_index(threat_types: dict[str, int], total: int) -> float:
    if not total or not threat_types:
        return 0.0
    weighted = 0.0
    for threat, count in threat_types.items():
        weight = THREAT_SEVERITY_WEIGHTS.get(threat, 0.3)
        weighted += weight * count
    return clamp(weighted / total)


def velocity_factor_guarded(metric: dict, min_events: int = 10) -> float:
    total = metric.get("total", 0)
    if total < min_events:
        return 0.0
    hourly_values = hourly_counts_chronological(metric.get("hourly") or {})
    baseline = sum(hourly_values) / len(hourly_values)
    current = latest_hourly_count(metric.get("hourly") or {})
    if baseline:
        velocity_spike = current / baseline
    elif current:
        velocity_spike = float(current)
    else:
        velocity_spike = 1.0
    return clamp((velocity_spike - 1.0) / 3.0)


def policy_escalation_factor(metric: dict) -> float:
    total = metric.get("total", 0)
    if not total:
        return 0.0
    return clamp((metric.get("policy_escalations", 0) / total) * 2.0)


def redact_learning_contribution(redact_rate: float) -> float:
    if redact_rate <= 0.5:
        return 0.0
    return min(0.05, (redact_rate - 0.5) * 0.1)


def _resolve_learning_weights(weights: dict[str, float] | None) -> dict[str, float]:
    default = {
        "block_rate": 0.50,
        "threat_severity": 0.25,
        "velocity": 0.15,
        "policy_escalation": 0.10,
    }
    if not weights:
        return default
    merged = {
        "block_rate": float(weights.get("block_rate", default["block_rate"])),
        "threat_severity": float(weights.get("threat_severity", default["threat_severity"])),
        "velocity": float(weights.get("velocity", default["velocity"])),
        "policy_escalation": float(weights.get("policy_escalation", default["policy_escalation"])),
    }
    total = sum(max(0.0, v) for v in merged.values())
    if total <= 0:
        return default
    return {k: max(0.0, v) / total for k, v in merged.items()}


def graduation_threshold_requests(key, org_settings) -> int:
    """Requests/prompts required before learning → active.

    Single org source of truth: UI ``behavior_profile_prompt_target``
    (default 50). Same number drives baseline profile building AND
    learning→active. Days are NOT a gate.

    Resolution order:
      1. per-key ``ueba_graduation_requests`` override (if set)
      2. org ``behavior_profile_prompt_target`` (UI prompt target)
      3. ``DEFAULT_GRADUATION_REQUESTS`` (50)
    """
    override = getattr(key, "ueba_graduation_requests", None)
    if override is not None:
        return int(override)

    if org_settings is not None:
        prompt_target = getattr(org_settings, "behavior_profile_prompt_target", None)
        if prompt_target is not None:
            return int(prompt_target)

    return DEFAULT_GRADUATION_REQUESTS


# Back-compat alias for callers that still import the old name.
def graduation_thresholds(key, org_settings) -> int:
    return graduation_threshold_requests(key, org_settings)


def is_graduated(key, org_settings, now: datetime | None = None) -> bool:
    """True when lifetime request count meets the prompt/request threshold.

    ``now`` is accepted for call-site compatibility but unused — graduation is
    not time-based.
    """
    del now  # prompts-only; calendar age must not graduate a key
    threshold = graduation_threshold_requests(key, org_settings)
    lifetime = int(getattr(key, "ueba_lifetime_request_count", 0) or 0)
    return lifetime >= threshold


def resolve_ueba_mode(key, org_settings, now: datetime | None = None) -> str:
    """Effective UEBA mode from graduation state (may differ from stale DB column)."""
    if is_graduated(key, org_settings, now):
        return "active"
    return "learning"


def graduation_progress(key, org_settings, now: datetime | None = None) -> dict[str, Any]:
    """Progress toward learning → active (requests only; no days field)."""
    del now
    threshold = graduation_threshold_requests(key, org_settings)
    lifetime = int(getattr(key, "ueba_lifetime_request_count", 0) or 0)
    req_pct = min(100.0, (lifetime / threshold) * 100.0) if threshold else 100.0
    return {
        "requests": lifetime,
        "thresholds": {
            "requests": threshold,
        },
        "pct_complete": round(req_pct, 1),
        "graduated": lifetime >= threshold,
    }


def risk_band_for_score(
    score: float,
    high_threshold: float = 0.70,
    medium_threshold: float = 0.35,
) -> str:
    if score >= high_threshold:
        return "high"
    if score >= medium_threshold:
        return "medium"
    return "low"


def _compute_block_deviation(current_block: float, avg_block: float, total: int, blocked_count: int = 0) -> float:
    if total < MIN_CURRENT_EVENTS:
        return 0.0
    if avg_block < LOW_BASELINE_EPS and blocked_count < 5:
        return 0.0
    delta = abs(current_block - avg_block)
    if avg_block < LOW_BASELINE_EPS:
        return min(delta / 0.10, 1.0)
    return delta / max(avg_block, 0.02)


def _compute_model_novelty(current_models: set, typical_models: set, baseline: Any) -> tuple[float, bool]:
    baseline_immature = (
        not typical_models
        or int(getattr(baseline, "sample_count", 0) or 0) < MIN_BASELINE_SAMPLES
    )
    if baseline_immature:
        return 0.0, True
    return len(current_models - typical_models) / max(len(typical_models), 1), False


def score_learning_mode(
    metric: dict,
    *,
    key_purpose: str | None = None,
    weights: dict[str, float] | None = None,
) -> tuple[float, dict[str, Any]]:
    total = metric.get("total", 0)
    block_rate = (metric.get("blocked", 0) / total) if total else 0.0
    redact_rate = (metric.get("redacted", 0) / total) if total else 0.0
    threat_idx = threat_severity_index(dict(metric.get("threat_types") or {}), total)
    velocity = velocity_factor_guarded(metric)
    policy_esc = policy_escalation_factor(metric)
    redact_contrib = redact_learning_contribution(redact_rate)

    resolved_weights = _resolve_learning_weights(weights)
    score = clamp(
        resolved_weights["block_rate"] * block_rate
        + resolved_weights["threat_severity"] * threat_idx
        + resolved_weights["velocity"] * velocity
        + resolved_weights["policy_escalation"] * policy_esc
        + redact_contrib
    )

    band_cap = None
    if key_purpose == "scanner":
        band_cap = "medium"
    elif total < 5:
        band_cap = "medium"

    anomalies: list[str] = []
    if block_rate >= 0.35:
        anomalies.append("high_block_rate")
    hourly_values = hourly_counts_chronological(metric.get("hourly") or {})
    hourly_avg = sum(hourly_values) / len(hourly_values) if hourly_values else 0
    current = latest_hourly_count(metric.get("hourly") or {})
    velocity_spike = (current / hourly_avg) if hourly_avg else (float(current) if current else 1.0)
    if velocity_spike >= 2.5:
        anomalies.append("velocity_spike")
    if len(metric.get("models") or []) >= 4:
        anomalies.append("model_spread")

    breakdown = {
        "mode": "learning",
        "block_rate": round(block_rate, 4),
        "threat_severity_index": round(threat_idx, 4),
        "velocity_factor": round(velocity, 4),
        "policy_escalation": round(policy_esc, 4),
        "redact_contribution": round(redact_contrib, 4),
        "weights": {
            "block_rate": round(resolved_weights["block_rate"], 4),
            "threat_severity": round(resolved_weights["threat_severity"], 4),
            "velocity": round(resolved_weights["velocity"], 4),
            "policy_escalation": round(resolved_weights["policy_escalation"], 4),
        },
        "velocity_spike": round(velocity_spike, 2),
        "anomaly_flags": anomalies,
        "band_cap": band_cap,
    }
    if key_purpose == "scanner":
        breakdown["scanner_learning_cap"] = True
    return round(score, 4), breakdown


def score_active_mode(metric: dict, baseline: Any) -> tuple[float, dict[str, Any]]:
    total = metric.get("total", 0)
    current_block = (metric.get("blocked", 0) / total) if total else 0.0
    current_redact = (metric.get("redacted", 0) / total) if total else 0.0
    avg_block = float(getattr(baseline, "avg_block_rate", 0) or 0)
    avg_redact = float(getattr(baseline, "avg_redact_rate", 0) or 0)
    avg_rph = float(getattr(baseline, "avg_requests_per_hour", 0) or 0)
    std_rph = float(getattr(baseline, "std_requests_per_hour", 0) or 0)
    typical_models = set(getattr(baseline, "typical_models", None) or [])
    current_models = set(metric.get("models") or [])

    block_dev = _compute_block_deviation(current_block, avg_block, total, int(metric.get("blocked", 0) or 0))
    block_term = 0.45 * sigmoid(block_dev) if total >= MIN_CURRENT_EVENTS else 0.0

    current_hour_count = latest_hourly_count(metric.get("hourly") or {})
    volume_z = (current_hour_count - avg_rph) / max(std_rph, 1.0)
    model_novelty, baseline_immature = _compute_model_novelty(current_models, typical_models, baseline)
    threat_idx = threat_severity_index(dict(metric.get("threat_types") or {}), total)
    redact_dev = min(0.10, 0.10 * abs(current_redact - avg_redact))

    score = clamp(
        block_term
        + 0.30 * sigmoid(volume_z / 2.0)
        + 0.15 * model_novelty
        + 0.10 * threat_idx
        + redact_dev
    )

    anomalies: list[str] = []
    if block_dev >= 2.0 and total >= MIN_CURRENT_EVENTS:
        anomalies.append("block_rate_deviation")
    if volume_z >= 3.0:
        anomalies.append("volume_anomaly")
    if model_novelty >= 0.5 and not baseline_immature:
        anomalies.append("model_novelty")

    breakdown = {
        "mode": "active",
        "block_deviation": round(block_dev, 4),
        "volume_z": round(volume_z, 4),
        "model_novelty": round(model_novelty, 4),
        "threat_severity_index": round(threat_idx, 4),
        "redact_deviation": round(redact_dev, 4),
        "current_block_rate": round(current_block, 4),
        "baseline_block_rate": round(avg_block, 4),
        "anomaly_flags": anomalies,
    }
    if baseline_immature:
        breakdown["baseline_immature"] = True
    return round(score, 4), breakdown


def blend_final_score(traditional_score: float, llm_score: float | None) -> float:
    """Legacy blend when llm_score = traditional + raw adjustment."""
    if llm_score is None:
        return traditional_score
    return round(clamp(0.55 * traditional_score + 0.45 * llm_score), 4)


def apply_llm_adjustment(traditional_score: float, score_adjustment: float) -> float:
    return round(clamp(traditional_score + score_adjustment), 4)


def apply_llm_blend(
    traditional_score: float,
    score_adjustment: float | None,
) -> tuple[float, float | None, float, float | None]:
    """
    final = traditional + LLM_BLEND_WEIGHT * adjustment
    Returns (traditional, llm_score_for_api, final, llm_weighted_delta).
    """
    if score_adjustment is None:
        return traditional_score, None, traditional_score, None
    weighted_delta = LLM_BLEND_WEIGHT * score_adjustment
    final = round(clamp(traditional_score + weighted_delta), 4)
    llm_score = apply_llm_adjustment(traditional_score, score_adjustment)
    return traditional_score, llm_score, final, round(weighted_delta, 4)


def baseline_deviation_factor(metric: dict, behavior_profile) -> tuple[float, dict[str, Any]]:
    if behavior_profile is None:
        return 0.0, {}
    baseline = dict(getattr(behavior_profile, "baseline_metrics", {}) or {})
    if not baseline:
        return 0.0, {}

    total = int(metric.get("total", 0) or 0)
    current_block = (metric.get("blocked", 0) / total) if total else 0.0
    current_redact = (metric.get("redacted", 0) / total) if total else 0.0
    baseline_block = float(baseline.get("block_rate", 0.0) or 0.0)
    baseline_redact = float(baseline.get("redact_rate", 0.0) or 0.0)

    block_delta = abs(current_block - baseline_block) / max(baseline_block, 0.05)
    redact_delta = abs(current_redact - baseline_redact) / max(baseline_redact, 0.05)
    block_deviation = clamp(block_delta)
    redact_deviation = clamp(redact_delta)

    baseline_models = {str(m).strip() for m in baseline.get("models", []) if str(m).strip()}
    current_models = {str(m).strip() for m in (metric.get("models") or []) if str(m).strip()}
    model_novelty = (
        clamp(len(current_models - baseline_models) / max(len(baseline_models), 1))
        if baseline_models else 0.0
    )

    baseline_top = str((baseline.get("top_threats") or [["none", 0]])[0][0] or "none")
    current_top = (
        sorted((metric.get("threat_types") or {}).items(), key=lambda x: -x[1])[0][0]
        if metric.get("threat_types") else "none"
    )
    threat_shift = 0.5 if baseline_top != current_top and current_top != "none" else 0.0

    factor = clamp(
        0.50 * block_deviation
        + 0.25 * model_novelty
        + 0.15 * redact_deviation
        + 0.10 * threat_shift
    )
    breakdown = {
        "baseline_block_rate": round(baseline_block, 4),
        "current_block_rate": round(current_block, 4),
        "baseline_redact_rate": round(baseline_redact, 4),
        "current_redact_rate": round(current_redact, 4),
        "model_novelty": round(model_novelty, 4),
        "threat_shift": round(threat_shift, 4),
        "factor": round(factor, 4),
    }
    return round(factor, 4), breakdown


def compute_traditional_score(
    metric: dict,
    *,
    key_purpose: str | None = None,
    weights: dict[str, float] | None = None,
    **_legacy_kwargs,
) -> tuple[float, dict[str, Any]]:
    """Always use learning-mode traditional scoring (active/graduation deprecated)."""
    return score_learning_mode(metric, key_purpose=key_purpose, weights=weights)
