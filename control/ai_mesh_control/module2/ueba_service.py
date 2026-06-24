"""UEBA v2 orchestration: baselines, assessments, graduation."""

from __future__ import annotations

import logging
import statistics
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from module2.models import ApiKeyBehaviorBaseline, ApiKeyRiskAssessment, OrgUebaSettings
from module2.ueba_llm_analyst import (
    build_triage_context,
    finalize_assessment_scores,
    run_llm_triage,
    should_run_llm_triage,
)
from module2.ueba_metrics import collect_key_metrics, empty_key_metric, hourly_counts_chronological
from module2.ueba_scoring import (
    MIN_BASELINE_SAMPLES,
    compute_traditional_score,
    graduation_progress,
    is_graduated,
    resolve_ueba_mode,
    risk_band_for_score,
)

LOG = logging.getLogger(__name__)

BASELINE_WINDOW_DAYS = 7
SCORING_WINDOW_HOURS = 24


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
    for label, value in (
        ("medium_risk_threshold", med),
        ("high_risk_threshold", high),
        ("llm_triage_min_traditional_score", llm_min),
    ):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{label} must be between 0 and 1.")
    if med >= high:
        raise ValueError("medium_risk_threshold must be less than high_risk_threshold.")
    if int(settings_obj.graduation_min_requests) < 1:
        raise ValueError("graduation_min_requests must be at least 1.")
    if float(settings_obj.graduation_min_days) <= 0:
        raise ValueError("graduation_min_days must be greater than 0.")
    if int(getattr(settings_obj, "scanner_graduation_min_requests", 10)) < 1:
        raise ValueError("scanner_graduation_min_requests must be at least 1.")
    if float(getattr(settings_obj, "scanner_graduation_min_days", 1.0)) <= 0:
        raise ValueError("scanner_graduation_min_days must be greater than 0.")


def _risk_metric_extras(breakdown: dict, metric: dict) -> dict:
    return {
        "velocity_spike": breakdown.get("velocity_spike", breakdown.get("volume_z", 1.0)),
        "volume_z": breakdown.get("volume_z"),
    }


def compute_baseline_from_metric(metric: dict, window_days: int = BASELINE_WINDOW_DAYS) -> dict:
    total = metric.get("total", 0)
    block_rate = (metric.get("blocked", 0) / total) if total else 0.0
    redact_rate = (metric.get("redacted", 0) / total) if total else 0.0
    hourly_values = hourly_counts_chronological(metric.get("hourly") or {})
    avg_rph = sum(hourly_values) / len(hourly_values) if hourly_values else 0.0
    std_rph = statistics.pstdev(hourly_values) if len(hourly_values) > 1 else 0.0
    typical_models = sorted(
        (metric.get("model_counts") or {}).items(),
        key=lambda x: -int(x[1]),
    )[:10]
    typical_models = [m for m, _ in typical_models] or list(metric.get("models") or [])
    return {
        "window_days": window_days,
        "avg_requests_per_hour": avg_rph,
        "std_requests_per_hour": std_rph,
        "avg_block_rate": block_rate,
        "avg_redact_rate": redact_rate,
        "typical_models": typical_models,
        "typical_threat_types": dict(metric.get("threat_types") or {}),
        "sample_count": total,
    }


def refresh_baseline_for_key(key, events_qs, window_days: int = BASELINE_WINDOW_DAYS, *, force: bool = False):
    """Build baseline from events in [now - window_days - 24h, now - 24h) — exclusive of scoring window."""
    if not force and getattr(key, "ueba_baseline_locked_at", None):
        existing = _load_baseline_for_key(key)
        if existing and int(existing.sample_count or 0) >= MIN_BASELINE_SAMPLES:
            return existing

    now = timezone.now()
    scoring_end = now - timedelta(hours=SCORING_WINDOW_HOURS)
    since = scoring_end - timedelta(days=window_days)
    window_events = events_qs.filter(created_at__gte=since, created_at__lt=scoring_end)
    _, metrics = collect_key_metrics([key], window_events)
    metric = metrics.get(key.prefix) or empty_key_metric()
    if metric["total"] < 1:
        return None
    payload = compute_baseline_from_metric(metric, window_days)
    baseline, _ = ApiKeyBehaviorBaseline.objects.update_or_create(
        gateway_api_key=key,
        defaults=payload,
    )
    return baseline


def _load_baseline_for_key(key) -> ApiKeyBehaviorBaseline | None:
    baseline = getattr(key, "ueba_baseline", None)
    if baseline is not None:
        return baseline
    try:
        return key.ueba_baseline
    except ApiKeyBehaviorBaseline.DoesNotExist:
        return ApiKeyBehaviorBaseline.objects.filter(gateway_api_key=key).first()


def assess_api_key(
    key,
    metric: dict,
    org_settings,
    baseline: ApiKeyBehaviorBaseline | None = None,
    *,
    run_llm: bool | None = None,
    active_kill_switches: list | None = None,
    now=None,
):
    now = now or timezone.now()
    progress = graduation_progress(key, org_settings, now)
    ueba_mode = resolve_ueba_mode(key, org_settings, now)
    baseline = baseline if baseline is not None else _load_baseline_for_key(key)

    scoring_mode = ueba_mode
    if ueba_mode == "active" and baseline is None:
        scoring_mode = "learning"

    traditional_score, breakdown = compute_traditional_score(
        scoring_mode,
        metric,
        baseline if scoring_mode == "active" else None,
        key_purpose=getattr(key, "key_purpose", None),
    )
    if scoring_mode != ueba_mode:
        breakdown = {**breakdown, "scoring_deferred": "awaiting_baseline", "ueba_mode": ueba_mode}
    if breakdown.get("band_cap") == "medium" and traditional_score > 0.69:
        traditional_score = 0.69

    llm_result = None
    do_llm = run_llm if run_llm is not None else should_run_llm_triage(traditional_score, breakdown, org_settings)
    if do_llm:
        context = build_triage_context(
            key, metric, baseline, traditional_score, breakdown, org_settings, active_kill_switches
        )
        llm_result = run_llm_triage(context)

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
        "ueba_mode": ueba_mode,
        "traditional_score": traditional,
        "llm_score": llm_score,
        "final_score": final_score,
        "risk_band": risk_band,
        "score_breakdown": breakdown,
        "llm_verdict": llm_verdict,
        "llm_confidence": llm_result.get("confidence") if llm_result else None,
        "llm_reasoning": llm_result.get("reasoning", "") if llm_result else "",
        "llm_recommended_action": llm_result.get("recommended_action", "") if llm_result else "",
        "graduation_progress": progress,
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
    mode = assessment["ueba_mode"]
    update_fields = {
        "ueba_mode": mode,
        "risk_score": assessment["final_score"],
    }
    if mode == "active" and not key.ueba_baseline_locked_at:
        update_fields["ueba_baseline_locked_at"] = assessment["computed_at"]

    from core.models import GatewayAPIKey

    GatewayAPIKey.objects.filter(pk=key.pk).update(**update_fields)

    snapshot = ApiKeyRiskAssessment.objects.create(
        gateway_api_key=key,
        computed_at=assessment["computed_at"],
        ueba_mode=assessment["ueba_mode"],
        traditional_score=assessment["traditional_score"],
        llm_score=assessment["llm_score"],
        final_score=assessment["final_score"],
        risk_band=assessment["risk_band"],
        score_breakdown=assessment["score_breakdown"],
        llm_verdict=assessment["llm_verdict"],
        llm_confidence=assessment["llm_confidence"],
        llm_reasoning=assessment["llm_reasoning"],
        llm_recommended_action=assessment["llm_recommended_action"],
        graduation_progress=assessment["graduation_progress"],
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

    from module2.ueba_metrics import collect_key_metrics, count_lifetime_events_by_prefix, empty_key_metric
    from module2.ueba_scoring import is_graduated

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

    if is_graduated(key, org_settings) and key.ueba_mode != "active":
        key.ueba_mode = "active"
        key.save(update_fields=["ueba_mode"])

    baseline = _load_baseline_for_key(key)
    if key.ueba_mode == "active" and baseline is None:
        baseline = refresh_baseline_for_key(key, all_events)
        if baseline is None:
            baseline = _load_baseline_for_key(key)

    kill_switches = list(
        KillSwitch.objects.filter(organization=key.organization, is_active=True, api_key_prefix=key.prefix).values(
            "model_name", "action", "reason"
        )
    )
    assessment = assess_api_key(
        key,
        metric,
        org_settings,
        baseline=baseline,
        run_llm=run_llm,
        active_kill_switches=kill_switches,
    )
    snapshot = persist_assessment(key, assessment)
    return snapshot, metric


def assessment_to_risk_payload(key, metric: dict, assessment: ApiKeyRiskAssessment | dict | None, org_settings=None):
    """Build API-facing risk dict from snapshot + live window metrics."""
    org_settings = org_settings or get_or_create_org_settings(key.organization)
    if assessment is None:
        from module2.ueba_scoring import score_learning_mode

        score, breakdown = score_learning_mode(metric, key_purpose=getattr(key, "key_purpose", None))
        if breakdown.get("band_cap") == "medium" and score > 0.69:
            score = 0.69
        total = metric.get("total", 0)
        block_rate = (metric.get("blocked", 0) / total) if total else 0.0
        redact_rate = (metric.get("redacted", 0) / total) if total else 0.0
        high_t, med_t = _band_thresholds(org_settings)
        risk_band = risk_band_for_score(score, high_t, med_t)
        if breakdown.get("band_cap") == "medium" and risk_band == "high":
            risk_band = "medium"
        progress = graduation_progress(key, org_settings) if org_settings else {}
        extras = _risk_metric_extras(breakdown, metric)
        return {
            "key_id": str(key.id),
            "prefix": key.prefix,
            "name": key.name,
            "project_id": key.project_id,
            "is_active": key.is_active,
            "key_purpose": key.key_purpose,
            "ueba_mode": resolve_ueba_mode(key, org_settings) if org_settings else key.ueba_mode,
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
            "graduation_progress": progress,
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
        }

    if isinstance(assessment, dict):
        a = assessment
    else:
        a = {
            "ueba_mode": assessment.ueba_mode,
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
            "graduation_progress": assessment.graduation_progress,
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
    return {
        "key_id": str(key.id),
        "prefix": key.prefix,
        "name": key.name,
        "project_id": key.project_id,
        "is_active": key.is_active,
        "key_purpose": key.key_purpose,
        "ueba_mode": resolve_ueba_mode(key, org_settings) if org_settings else a.get("ueba_mode", key.ueba_mode),
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
        "graduation_progress": a.get("graduation_progress", {}),
        "request_count": total,
        "blocked_count": metric.get("blocked", 0),
        "redacted_count": metric.get("redacted", 0),
        "block_rate_pct": round(block_rate * 100, 1),
        "redact_rate_pct": round(redact_rate * 100, 1),
        "unique_endpoints": len(metric.get("endpoint_ids") or []),
        "unique_models": len(metric.get("models") or []),
        "top_threat_type": top_threat,
    }
