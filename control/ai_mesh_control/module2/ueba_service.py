"""UEBA orchestration: traditional scoring + LLM behavior profile + triage."""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from module2.models import ApiKeyRiskAssessment, OrgUebaSettings
from module2.ueba_behavior_profile import (
    behavior_profile_payload,
    get_or_create_profile,
    load_profile_for_key,
    needs_bootstrap,
    profile_is_ready,
    save_bootstrap_result,
)
from module2.ueba_llm_analyst import (
    build_bootstrap_context,
    build_triage_context,
    finalize_assessment_scores,
    run_llm_behavior_bootstrap,
    run_llm_triage,
    should_run_llm_triage,
)
from module2.ueba_metrics import collect_key_metrics, empty_key_metric
from module2.ueba_scoring import (
    LLM_BLEND_WEIGHT,
    baseline_deviation_factor,
    clamp,
    compute_traditional_score,
    risk_band_for_score,
    score_learning_mode,
)

LOG = logging.getLogger(__name__)

SCORING_WINDOW_HOURS = 24
RECENT_BEHAVIOR_EVENTS = 10
_LLM_RATE_BUCKETS: dict[int, list[float]] = {}

TRADITIONAL_WEIGHT_MAX = 1.0
TRADITIONAL_WEIGHT_MIN_SUM = 0.5
BASELINE_DEVIATION_WEIGHT_MAX = 0.5
PROMPT_TARGET_MIN = 10
PROMPT_TARGET_MAX = 500


def get_or_create_org_settings(org):
    if org is None:
        return None
    settings_obj, _ = OrgUebaSettings.objects.get_or_create(organization=org)
    return settings_obj


def latest_assessment_for_key(key):
    return (
        ApiKeyRiskAssessment.objects.filter(gateway_api_key=key)
        .order_by("-computed_at")
        .first()
    )


def _band_thresholds(org_settings):
    if org_settings is None:
        return 0.70, 0.35
    return org_settings.high_risk_threshold, org_settings.medium_risk_threshold


def validate_org_ueba_settings(settings_obj) -> None:
    """Raise ValueError when org UEBA thresholds are out of range."""
    med = float(settings_obj.medium_risk_threshold)
    high = float(settings_obj.high_risk_threshold)
    llm_min = float(settings_obj.llm_triage_min_traditional_score)
    prompt_target = int(getattr(settings_obj, "behavior_profile_prompt_target", 50))
    weight_baseline = float(getattr(settings_obj, "weight_baseline_deviation", 0.2))
    traditional = (
        ("weight_block_rate", float(getattr(settings_obj, "weight_block_rate", 0.5))),
        ("weight_threat_severity", float(getattr(settings_obj, "weight_threat_severity", 0.25))),
        ("weight_velocity", float(getattr(settings_obj, "weight_velocity", 0.15))),
        ("weight_policy_escalation", float(getattr(settings_obj, "weight_policy_escalation", 0.1))),
    )
    for label, value in (
        ("medium_risk_threshold", med),
        ("high_risk_threshold", high),
        ("llm_triage_min_traditional_score", llm_min),
        ("weight_baseline_deviation", weight_baseline),
    ):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{label} must be between 0 and 1.")
    if weight_baseline > BASELINE_DEVIATION_WEIGHT_MAX:
        raise ValueError(
            f"weight_baseline_deviation must be <= {BASELINE_DEVIATION_WEIGHT_MAX}."
        )
    for label, value in traditional:
        if not 0.0 <= value <= TRADITIONAL_WEIGHT_MAX:
            raise ValueError(f"{label} must be between 0 and {TRADITIONAL_WEIGHT_MAX}.")
    traditional_sum = sum(value for _, value in traditional)
    if traditional_sum < TRADITIONAL_WEIGHT_MIN_SUM:
        raise ValueError(
            f"Traditional metric weights must sum to at least {TRADITIONAL_WEIGHT_MIN_SUM}."
        )
    if med >= high:
        raise ValueError("medium_risk_threshold must be less than high_risk_threshold.")
    if prompt_target < PROMPT_TARGET_MIN or prompt_target > PROMPT_TARGET_MAX:
        raise ValueError(
            f"behavior_profile_prompt_target must be between {PROMPT_TARGET_MIN} and {PROMPT_TARGET_MAX}."
        )


def _learning_weight_settings(org_settings) -> dict[str, float]:
    if org_settings is None:
        return {
            "block_rate": 0.50,
            "threat_severity": 0.25,
            "velocity": 0.15,
            "policy_escalation": 0.10,
        }
    return {
        "block_rate": float(getattr(org_settings, "weight_block_rate", 0.50)),
        "threat_severity": float(getattr(org_settings, "weight_threat_severity", 0.25)),
        "velocity": float(getattr(org_settings, "weight_velocity", 0.15)),
        "policy_escalation": float(getattr(org_settings, "weight_policy_escalation", 0.10)),
    }


def risk_calc_settings_payload(org_settings) -> dict:
    weights = {
        "block_rate": float(getattr(org_settings, "weight_block_rate", 0.50)),
        "threat_severity": float(getattr(org_settings, "weight_threat_severity", 0.25)),
        "velocity": float(getattr(org_settings, "weight_velocity", 0.15)),
        "policy_escalation": float(getattr(org_settings, "weight_policy_escalation", 0.10)),
        "baseline_deviation": float(getattr(org_settings, "weight_baseline_deviation", 0.20)),
    }
    traditional_sum = (
        weights["block_rate"]
        + weights["threat_severity"]
        + weights["velocity"]
        + weights["policy_escalation"]
    )
    return {
        "behavior_profile_prompt_target": int(getattr(org_settings, "behavior_profile_prompt_target", 50)),
        "llm_triage_enabled": bool(getattr(org_settings, "llm_triage_enabled", True)),
        "llm_triage_min_traditional_score": float(getattr(org_settings, "llm_triage_min_traditional_score", 0.45)),
        "high_risk_threshold": float(getattr(org_settings, "high_risk_threshold", 0.70)),
        "medium_risk_threshold": float(getattr(org_settings, "medium_risk_threshold", 0.35)),
        "weights": weights,
        "weight_guardrails": {
            "traditional_weight_max": TRADITIONAL_WEIGHT_MAX,
            "traditional_weight_min_sum": TRADITIONAL_WEIGHT_MIN_SUM,
            "traditional_weight_sum": round(traditional_sum, 4),
            "baseline_deviation_weight_max": BASELINE_DEVIATION_WEIGHT_MAX,
            "prompt_target_min": PROMPT_TARGET_MIN,
            "prompt_target_max": PROMPT_TARGET_MAX,
            "traditional_weights_normalized_at_score_time": True,
        },
    }


def risk_calc_formula_reference(org_settings) -> dict[str, Any]:
    high_t = float(getattr(org_settings, "high_risk_threshold", 0.70))
    med_t = float(getattr(org_settings, "medium_risk_threshold", 0.35))
    return {
        "traditional": (
            "score = w_block*block_rate + w_threat*threat_idx + w_velocity*velocity "
            + "+ w_policy*policy_esc + redact_bonus"
        ),
        "post_profile": "traditional += weight_baseline_deviation * behavior_deviation_factor",
        "final": f"final = traditional + {LLM_BLEND_WEIGHT} * llm_adjustment (when LLM runs)",
        "llm_blend_weight": LLM_BLEND_WEIGHT,
        "bands": {"high": high_t, "medium": med_t},
    }


def snapshot_org_ueba_settings(org_settings) -> dict[str, Any]:
    return risk_calc_settings_payload(org_settings)


def diff_org_ueba_settings(before: dict, after: dict) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    scalar_keys = (
        "behavior_profile_prompt_target",
        "llm_triage_enabled",
        "llm_triage_min_traditional_score",
        "high_risk_threshold",
        "medium_risk_threshold",
    )
    for key in scalar_keys:
        if before.get(key) != after.get(key):
            changes[key] = {"from": before.get(key), "to": after.get(key)}
    before_weights = before.get("weights") or {}
    after_weights = after.get("weights") or {}
    for weight_key, new_value in after_weights.items():
        old_value = before_weights.get(weight_key)
        if old_value != new_value:
            changes[f"weights.{weight_key}"] = {"from": old_value, "to": new_value}
    return changes


def llm_observation_payload(
    metric: dict,
    org_settings,
    *,
    traditional_score: float,
    breakdown: dict,
    behavior_profile: dict,
) -> dict[str, Any]:
    """Gate flags for LLM profile bootstrap vs triage based on org score-calculation settings."""
    prompt_target = int(getattr(org_settings, "behavior_profile_prompt_target", 50))
    triage_threshold = float(getattr(org_settings, "llm_triage_min_traditional_score", 0.45))
    llm_enabled = bool(getattr(org_settings, "llm_triage_enabled", True))
    request_count = int(metric.get("total", 0) or 0)
    collected = int(behavior_profile.get("prompt_samples_collected") or 0)
    profile_ready = behavior_profile.get("status") == "ready"
    requests_meet_prompt_target = request_count >= prompt_target or collected >= prompt_target
    triage_score_gate_met = float(traditional_score) >= triage_threshold
    anomaly_flags = list(breakdown.get("anomaly_flags") or [])
    triage_eligible = (
        profile_ready
        and llm_enabled
        and (triage_score_gate_met or bool(anomaly_flags))
    )
    if triage_eligible:
        phase = "triage_eligible"
    elif not profile_ready:
        phase = "profile_building"
    elif not llm_enabled:
        phase = "llm_disabled"
    elif triage_score_gate_met or anomaly_flags:
        phase = "monitoring"
    else:
        phase = "below_triage_threshold"
    return {
        "prompt_target": prompt_target,
        "triage_threshold": round(triage_threshold, 4),
        "request_count": request_count,
        "prompt_samples_collected": collected,
        "requests_meet_prompt_target": requests_meet_prompt_target,
        "requests_below_prompt_target": not requests_meet_prompt_target,
        "triage_score_gate_met": triage_score_gate_met,
        "profile_ready": profile_ready,
        "llm_triage_enabled": llm_enabled,
        "llm_triage_eligible": triage_eligible,
        "observation_phase": phase,
    }


def write_ueba_settings_audit_log(*, user, org, changes: dict[str, dict[str, Any]], ip=None) -> None:
    if not changes:
        return
    from core.models import AuditLog

    try:
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            organization=org,
            action="ueba_risk_settings_update",
            resource=f"module2:ueba_settings:{getattr(org, 'id', '')}",
            details=json.dumps({"changes": changes}, sort_keys=True, default=str),
            ip_address=ip,
        )
    except Exception:
        LOG.exception("Failed to write UEBA settings audit log org=%s", getattr(org, "id", None))


def update_org_ueba_settings(org_settings, payload: dict):
    mapping = {
        "behavior_profile_prompt_target": "behavior_profile_prompt_target",
        "llm_triage_enabled": "llm_triage_enabled",
        "llm_triage_min_traditional_score": "llm_triage_min_traditional_score",
        "high_risk_threshold": "high_risk_threshold",
        "medium_risk_threshold": "medium_risk_threshold",
    }
    for src, dst in mapping.items():
        if src in payload:
            setattr(org_settings, dst, payload[src])
    weights = payload.get("weights") or {}
    if isinstance(weights, dict):
        if "block_rate" in weights:
            org_settings.weight_block_rate = float(weights["block_rate"])
        if "threat_severity" in weights:
            org_settings.weight_threat_severity = float(weights["threat_severity"])
        if "velocity" in weights:
            org_settings.weight_velocity = float(weights["velocity"])
        if "policy_escalation" in weights:
            org_settings.weight_policy_escalation = float(weights["policy_escalation"])
        if "baseline_deviation" in weights:
            org_settings.weight_baseline_deviation = float(weights["baseline_deviation"])
    validate_org_ueba_settings(org_settings)
    org_settings.save()
    return org_settings


def apply_org_ueba_settings_update(
    org_settings,
    payload: dict,
    *,
    user=None,
    org=None,
    ip=None,
) -> tuple[Any, dict[str, dict[str, Any]]]:
    """Validate, persist, audit, and optionally queue reassessment for setting changes."""
    before = snapshot_org_ueba_settings(org_settings)
    update_org_ueba_settings(org_settings, payload)
    after = snapshot_org_ueba_settings(org_settings)
    changes = diff_org_ueba_settings(before, after)
    if changes and org is not None:
        write_ueba_settings_audit_log(user=user, org=org, changes=changes, ip=ip)
    if changes and org is not None:
        from module2.tasks import reassess_org_ueba_keys

        org_settings.refresh_from_db()
        reassess_org_ueba_keys.delay(org.id, run_llm=bool(org_settings.llm_triage_enabled))
    return org_settings, changes


def _risk_metric_extras(breakdown: dict, metric: dict) -> dict:
    return {
        "velocity_spike": breakdown.get("velocity_spike", breakdown.get("volume_z", 1.0)),
        "volume_z": breakdown.get("volume_z"),
    }


def _llm_rate_limit_ok(org_id: int | None) -> bool:
    if org_id is None:
        return True
    max_per_min = int(getattr(settings, "MODULE2_UEBA_LLM_MAX_PER_MIN", 10))
    now = time.monotonic()
    bucket = _LLM_RATE_BUCKETS.setdefault(org_id, [])
    bucket[:] = [t for t in bucket if now - t < 60.0]
    if len(bucket) >= max_per_min:
        return False
    bucket.append(now)
    return True


def _apply_band_cap(traditional_score: float, breakdown: dict) -> float:
    if breakdown.get("band_cap") == "medium" and traditional_score > 0.69:
        return 0.69
    return traditional_score


def assess_api_key(
    key,
    metric: dict,
    org_settings,
    *,
    run_llm: bool | None = None,
    active_kill_switches: list | None = None,
    behavior_profile=None,
    now=None,
):
    now = now or timezone.now()
    profile = behavior_profile if behavior_profile is not None else load_profile_for_key(key)
    if profile is None:
        profile = get_or_create_profile(key)

    traditional_score, breakdown = compute_traditional_score(
        metric,
        key_purpose=getattr(key, "key_purpose", None),
        weights=_learning_weight_settings(org_settings),
    )
    traditional_score = _apply_band_cap(traditional_score, breakdown)
    breakdown = {**breakdown, "mode": "traditional"}

    bootstrap_adjustment = None
    llm_result = None

    org_id = getattr(key, "organization_id", None)
    llm_allowed = run_llm if run_llm is not None else True

    if llm_allowed and needs_bootstrap(profile, org_settings) and _llm_rate_limit_ok(org_id):
        if org_settings is None or org_settings.llm_triage_enabled:
            bootstrap_ctx = build_bootstrap_context(key, metric, profile, org_settings=org_settings)
            bootstrap = run_llm_behavior_bootstrap(bootstrap_ctx)
            if not bootstrap.get("degraded"):
                save_bootstrap_result(profile, bootstrap)
                bootstrap_adjustment = bootstrap.get("score_adjustment")
            else:
                LOG.warning("UEBA bootstrap degraded for key=%s", key.prefix)

    ready = profile_is_ready(profile)
    if ready:
        deviation_factor, deviation_breakdown = baseline_deviation_factor(metric, profile)
        deviation_weight = float(getattr(org_settings, "weight_baseline_deviation", 0.20))
        weighted_deviation = deviation_weight * deviation_factor
        traditional_score = clamp(traditional_score + weighted_deviation)
        breakdown = {
            **breakdown,
            "baseline_deviation_factor": round(deviation_factor, 4),
            "baseline_deviation_weight": round(deviation_weight, 4),
            "baseline_deviation_delta": round(weighted_deviation, 4),
            "baseline_deviation_breakdown": deviation_breakdown,
        }
    do_triage = False
    if llm_allowed and ready:
        do_triage = should_run_llm_triage(
            traditional_score,
            breakdown,
            org_settings,
            profile_ready=True,
        )
        if run_llm is False:
            do_triage = False

    if do_triage and _llm_rate_limit_ok(org_id):
        context = build_triage_context(
            key,
            metric,
            traditional_score,
            breakdown,
            org_settings,
            active_kill_switches,
            behavior_profile=profile,
        )
        llm_result = run_llm_triage(context)

    if bootstrap_adjustment is not None and llm_result is None:
        llm_result = {
            "verdict": "benign",
            "confidence": profile.llm_confidence,
            "reasoning": profile.risk_prediction or profile.expected_use_case,
            "recommended_action": "monitor",
            "score_adjustment": float(bootstrap_adjustment),
        }

    traditional, llm_score, final_score, llm_verdict, llm_weighted_delta = finalize_assessment_scores(
        traditional_score, llm_result
    )
    if llm_weighted_delta is not None:
        breakdown = {**breakdown, "llm_weighted_delta": llm_weighted_delta}

    high_t, med_t = _band_thresholds(org_settings)
    risk_band = risk_band_for_score(final_score, high_t, med_t)
    if breakdown.get("band_cap") == "medium" and risk_band == "high":
        risk_band = "medium"

    return {
        "computed_at": now,
        "traditional_score": traditional,
        "llm_score": llm_score,
        "final_score": final_score,
        "risk_band": risk_band,
        "score_breakdown": breakdown,
        "llm_verdict": llm_verdict,
        "llm_confidence": llm_result.get("confidence") if llm_result else None,
        "llm_reasoning": llm_result.get("reasoning", "") if llm_result else "",
        "llm_recommended_action": llm_result.get("recommended_action", "") if llm_result else "",
        "behavior_profile": behavior_profile_payload(profile, org_settings),
    }


def prune_assessment_history(key, keep: int | None = None) -> int:
    """Delete older snapshots beyond retention window for one key."""
    keep = keep or getattr(settings, "MODULE2_UEBA_ASSESSMENT_RETENTION_COUNT", 48)
    keep_ids = list(
        ApiKeyRiskAssessment.objects.filter(gateway_api_key=key)
        .order_by("-computed_at")
        .values_list("pk", flat=True)[:keep]
    )
    if not keep_ids:
        return 0
    deleted, _ = (
        ApiKeyRiskAssessment.objects.filter(gateway_api_key=key)
        .exclude(pk__in=keep_ids)
        .delete()
    )
    return deleted


@transaction.atomic
def persist_assessment(key, assessment: dict):
    from core.models import GatewayAPIKey

    GatewayAPIKey.objects.filter(pk=key.pk).update(risk_score=assessment["final_score"])

    snapshot = ApiKeyRiskAssessment.objects.create(
        gateway_api_key=key,
        computed_at=assessment["computed_at"],
        ueba_mode="learning",
        traditional_score=assessment["traditional_score"],
        llm_score=assessment["llm_score"],
        final_score=assessment["final_score"],
        risk_band=assessment["risk_band"],
        score_breakdown=assessment["score_breakdown"],
        llm_verdict=assessment["llm_verdict"],
        llm_confidence=assessment["llm_confidence"],
        llm_reasoning=assessment["llm_reasoning"],
        llm_recommended_action=assessment["llm_recommended_action"],
        graduation_progress={},
    )
    prune_assessment_history(key)
    return snapshot


def assessments_map_for_keys(keys) -> dict:
    """Latest ApiKeyRiskAssessment per gateway key id."""
    key_ids = [k.pk for k in keys]
    if not key_ids:
        return {}
    assessments = (
        ApiKeyRiskAssessment.objects.filter(gateway_api_key_id__in=key_ids)
        .order_by("gateway_api_key_id", "-computed_at")
        .distinct("gateway_api_key_id")
    )
    return {a.gateway_api_key_id: a for a in assessments}


def reassess_api_key(key, org_settings=None, *, run_llm: bool = True):
    from core.models import KillSwitch
    from policy.models import EnforcementEvent

    from module2.ueba_metrics import count_lifetime_events_by_prefix

    org_settings = org_settings or get_or_create_org_settings(key.organization)
    since = timezone.now() - timedelta(hours=SCORING_WINDOW_HOURS)
    all_events = EnforcementEvent.objects.filter(organization=key.organization)
    window_events = all_events.filter(created_at__gte=since)
    _, metrics = collect_key_metrics([key], window_events)
    metric = metrics.get(key.prefix) or empty_key_metric()
    lifetime = count_lifetime_events_by_prefix([key], all_events).get(key.prefix, 0)
    if key.ueba_lifetime_request_count != lifetime:
        key.ueba_lifetime_request_count = lifetime
        key.save(update_fields=["ueba_lifetime_request_count"])

    kill_switches = list(
        KillSwitch.objects.filter(organization=key.organization, is_active=True, api_key_prefix=key.prefix).values(
            "model_name", "action", "reason"
        )
    )
    assessment = assess_api_key(
        key,
        metric,
        org_settings,
        run_llm=run_llm,
        active_kill_switches=kill_switches,
    )
    snapshot = persist_assessment(key, assessment)
    return snapshot, metric


def assessment_to_risk_payload(key, metric: dict, assessment: ApiKeyRiskAssessment | dict | None, org_settings=None):
    """Build API-facing risk dict from snapshot + live window metrics."""
    org_settings = org_settings or get_or_create_org_settings(key.organization)
    profile = load_profile_for_key(key)
    behavior = behavior_profile_payload(profile, org_settings)

    if assessment is None:
        score, breakdown = score_learning_mode(
            metric,
            key_purpose=getattr(key, "key_purpose", None),
            weights=_learning_weight_settings(org_settings),
        )
        if profile_is_ready(profile):
            deviation_factor, deviation_breakdown = baseline_deviation_factor(metric, profile)
            deviation_weight = float(getattr(org_settings, "weight_baseline_deviation", 0.20))
            deviation_delta = deviation_weight * deviation_factor
            score = clamp(score + deviation_delta)
            breakdown = {
                **breakdown,
                "baseline_deviation_factor": round(deviation_factor, 4),
                "baseline_deviation_weight": round(deviation_weight, 4),
                "baseline_deviation_delta": round(deviation_delta, 4),
                "baseline_deviation_breakdown": deviation_breakdown,
            }
        score = _apply_band_cap(score, breakdown)
        total = metric.get("total", 0)
        block_rate = (metric.get("blocked", 0) / total) if total else 0.0
        redact_rate = (metric.get("redacted", 0) / total) if total else 0.0
        high_t, med_t = _band_thresholds(org_settings)
        risk_band = risk_band_for_score(score, high_t, med_t)
        if breakdown.get("band_cap") == "medium" and risk_band == "high":
            risk_band = "medium"
        extras = _risk_metric_extras(breakdown, metric)
        llm_observation = llm_observation_payload(
            metric,
            org_settings,
            traditional_score=score,
            breakdown=breakdown,
            behavior_profile=behavior,
        )
        return {
            "key_id": str(key.id),
            "prefix": key.prefix,
            "name": key.name,
            "project_id": key.project_id,
            "is_active": key.is_active,
            "key_purpose": key.key_purpose,
            "risk_band": risk_band,
            "risk_score": round(score, 3),
            "final_score": round(score, 3),
            "traditional_score": round(score, 3),
            "llm_score": None,
            "computed_at": None,
            "velocity_spike": extras["velocity_spike"],
            "volume_z": extras["volume_z"],
            "anomaly_flags": breakdown.get("anomaly_flags", []),
            "score_breakdown": breakdown,
            "llm_verdict": "skipped",
            "llm_confidence": None,
            "llm_reasoning": "",
            "llm_recommended_action": "",
            "behavior_profile": behavior,
            "request_count": total,
            "blocked_count": metric.get("blocked", 0),
            "redacted_count": metric.get("redacted", 0),
            "block_rate_pct": round(block_rate * 100, 1),
            "redact_rate_pct": round(redact_rate * 100, 1),
            "unique_endpoints": len(metric.get("endpoint_ids") or []),
            "unique_models": len(metric.get("models") or []),
            "top_threat_type": (
                sorted(metric.get("threat_types", {}).items(), key=lambda x: -x[1])[0][0]
                if metric.get("threat_types")
                else "none"
            ),
            "llm_observation": llm_observation,
        }

    if isinstance(assessment, dict):
        a = assessment
    else:
        a = {
            "risk_band": assessment.risk_band,
            "final_score": assessment.final_score,
            "traditional_score": assessment.traditional_score,
            "llm_score": assessment.llm_score,
            "computed_at": assessment.computed_at,
            "score_breakdown": assessment.score_breakdown,
            "llm_verdict": assessment.llm_verdict,
            "llm_confidence": assessment.llm_confidence,
            "llm_reasoning": assessment.llm_reasoning,
            "llm_recommended_action": assessment.llm_recommended_action,
        }

    breakdown = a.get("score_breakdown") or {}
    extras = _risk_metric_extras(breakdown, metric)
    total = metric.get("total", 0)
    block_rate = (metric.get("blocked", 0) / total) if total else 0.0
    redact_rate = (metric.get("redacted", 0) / total) if total else 0.0
    top_threat = (
        sorted(metric.get("threat_types", {}).items(), key=lambda x: -x[1])[0][0]
        if metric.get("threat_types")
        else "none"
    )
    if isinstance(assessment, dict) and assessment.get("behavior_profile"):
        behavior = assessment["behavior_profile"]

    llm_observation = llm_observation_payload(
        metric,
        org_settings,
        traditional_score=float(a.get("traditional_score", 0)),
        breakdown=breakdown,
        behavior_profile=behavior,
    )

    return {
        "key_id": str(key.id),
        "prefix": key.prefix,
        "name": key.name,
        "project_id": key.project_id,
        "is_active": key.is_active,
        "key_purpose": key.key_purpose,
        "risk_band": a.get("risk_band", "low"),
        "risk_score": round(float(a.get("final_score", 0)), 3),
        "final_score": round(float(a.get("final_score", 0)), 3),
        "traditional_score": round(float(a.get("traditional_score", 0)), 3),
        "llm_score": a.get("llm_score"),
        "computed_at": (
            a.get("computed_at").isoformat()
            if hasattr(a.get("computed_at"), "isoformat")
            else a.get("computed_at")
        ),
        "velocity_spike": extras["velocity_spike"],
        "volume_z": extras["volume_z"],
        "anomaly_flags": breakdown.get("anomaly_flags", []),
        "score_breakdown": breakdown,
        "llm_verdict": a.get("llm_verdict", "skipped"),
        "llm_confidence": a.get("llm_confidence"),
        "llm_reasoning": a.get("llm_reasoning", ""),
        "llm_recommended_action": a.get("llm_recommended_action", ""),
        "behavior_profile": behavior,
        "request_count": total,
        "blocked_count": metric.get("blocked", 0),
        "redacted_count": metric.get("redacted", 0),
        "block_rate_pct": round(block_rate * 100, 1),
        "redact_rate_pct": round(redact_rate * 100, 1),
        "unique_endpoints": len(metric.get("endpoint_ids") or []),
        "unique_models": len(metric.get("models") or []),
        "top_threat_type": top_threat,
        "llm_observation": llm_observation,
    }


def build_risk_rows(keys, key_by_prefix, metrics, org_settings=None):
    """Build API risk rows for a set of keys using cached assessments."""
    if not keys:
        return []
    org_settings = org_settings or get_or_create_org_settings(keys[0].organization)
    assessments = assessments_map_for_keys(keys)
    rows = []
    for prefix, metric in metrics.items():
        key_obj = key_by_prefix.get(prefix)
        if not key_obj:
            continue
        rows.append(
            assessment_to_risk_payload(
                key_obj,
                metric,
                assessments.get(key_obj.pk),
                org_settings,
            )
        )
    return rows
