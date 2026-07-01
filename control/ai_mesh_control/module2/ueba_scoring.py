"""Pure UEBA v2 scoring functions (learning, active deviation, graduation)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
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

SCANNER_DEFAULT_GRADUATION_REQUESTS = 10
SCANNER_DEFAULT_GRADUATION_DAYS = 1.0


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


def graduation_thresholds(
    key,
    org_settings,
) -> tuple[int, float]:
    purpose = getattr(key, "key_purpose", None)
    if purpose == "scanner":
        req = getattr(key, "ueba_graduation_requests", None)
        if req is None and org_settings is not None:
            req = getattr(org_settings, "scanner_graduation_min_requests", None)
        if req is None:
            req = SCANNER_DEFAULT_GRADUATION_REQUESTS
        days = getattr(key, "ueba_graduation_days", None)
        if days is None and org_settings is not None:
            days = getattr(org_settings, "scanner_graduation_min_days", None)
        if days is None:
            days = SCANNER_DEFAULT_GRADUATION_DAYS
        return int(req), float(days)

    req = key.ueba_graduation_requests
    if req is None and org_settings:
        req = org_settings.graduation_min_requests
    if req is None:
        req = 50
    days = key.ueba_graduation_days
    if days is None and org_settings:
        days = org_settings.graduation_min_days
    if days is None:
        days = 7.0
    return int(req), float(days)


def is_graduated(key, org_settings, now: datetime | None = None) -> bool:
    now = now or datetime.now(key.created_at.tzinfo)
    threshold_requests, threshold_days = graduation_thresholds(key, org_settings)
    lifetime = int(getattr(key, "ueba_lifetime_request_count", 0) or 0)
    age_days = (now - key.created_at).total_seconds() / 86400.0
    return lifetime >= threshold_requests or age_days >= threshold_days


def resolve_ueba_mode(key, org_settings, now: datetime | None = None) -> str:
    """Effective UEBA mode from graduation state (may differ from stale DB column)."""
    if is_graduated(key, org_settings, now):
        return "active"
    return "learning"


def graduation_progress(key, org_settings, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(key.created_at.tzinfo)
    threshold_requests, threshold_days = graduation_thresholds(key, org_settings)
    lifetime = int(getattr(key, "ueba_lifetime_request_count", 0) or 0)
    age_days = (now - key.created_at).total_seconds() / 86400.0
    req_pct = min(100.0, (lifetime / threshold_requests) * 100.0) if threshold_requests else 100.0
    day_pct = min(100.0, (age_days / threshold_days) * 100.0) if threshold_days else 100.0
    return {
        "requests": lifetime,
        "days": round(age_days, 2),
        "thresholds": {
            "requests": threshold_requests,
            "days": threshold_days,
        },
        "pct_complete": round(max(req_pct, day_pct), 1),
        "graduated": is_graduated(key, org_settings, now),
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


def score_learning_mode(metric: dict, *, key_purpose: str | None = None) -> tuple[float, dict[str, Any]]:
    total = metric.get("total", 0)
    block_rate = (metric.get("blocked", 0) / total) if total else 0.0
    redact_rate = (metric.get("redacted", 0) / total) if total else 0.0
    threat_idx = threat_severity_index(dict(metric.get("threat_types") or {}), total)
    velocity = velocity_factor_guarded(metric)
    policy_esc = policy_escalation_factor(metric)
    redact_contrib = redact_learning_contribution(redact_rate)

    score = clamp(
        0.50 * block_rate
        + 0.25 * threat_idx
        + 0.15 * velocity
        + 0.10 * policy_esc
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


def compute_traditional_score(
    ueba_mode: str,
    metric: dict,
    baseline: Any | None = None,
    *,
    key_purpose: str | None = None,
) -> tuple[float, dict[str, Any]]:
    if ueba_mode == "active" and baseline is not None:
        return score_active_mode(metric, baseline)
    return score_learning_mode(metric, key_purpose=key_purpose)
