"""
Module 2 Celery tasks — alert evaluation, anomaly detection, threat intel sync.
"""

import json
import logging
import statistics
from datetime import timedelta

import redis
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from ai_mesh_shared.redis_pool import connection_pool_kwargs

logger = logging.getLogger(__name__)

THREAT_INTEL_KEY_PREFIX = "firewall:threat_intel:"
THREAT_INTEL_CHANNEL = "threat_intel_updates"

_OPERATORS = {
    "gt": lambda a, b: a > b,
    "lt": lambda a, b: a < b,
    "gte": lambda a, b: a >= b,
    "lte": lambda a, b: a <= b,
}


def _get_redis_client():
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, **connection_pool_kwargs())


@shared_task(queue="compute.heavy", bind=True, max_retries=2)
def evaluate_alert_rules(self, org_id=None):
    """Evaluate alert rules for one org or all orgs."""
    from auth.models import Organization
    from module2.models import AlertFiring, AlertRule, PlaybookRun
    from policy.constants import ACTION_BLOCK, ACTION_REDACT
    from policy.models import EnforcementEvent, SecurityIncident

    orgs = Organization.objects.filter(pk=org_id) if org_id else Organization.objects.filter(is_active=True)
    for org in orgs:
        rules = AlertRule.objects.filter(organization=org, enabled=True)
        for rule in rules:
            since = timezone.now() - timedelta(seconds=rule.window_seconds)
            events = EnforcementEvent.objects.filter(organization=org, created_at__gte=since)
            total = events.count()
            if total == 0 and rule.metric != "incident_count":
                continue

            current_value = 0.0
            if rule.metric == "block_rate":
                current_value = events.filter(action=ACTION_BLOCK).count() / total * 100
            elif rule.metric == "pii_rate":
                pii = sum(
                    1 for ev in events.values("metadata")
                    if (ev.get("metadata") or {}).get("threat_type") in ("pii", "sensitive_data", "data_leakage")
                )
                current_value = pii / total * 100 if total else 0
            elif rule.metric == "tier2_score":
                scores = [
                    (ev.get("metadata") or {}).get("security_risk_score", 0)
                    for ev in events.values("metadata")
                ]
                current_value = max(scores) if scores else 0
            elif rule.metric == "incident_count":
                current_value = SecurityIncident.objects.filter(
                    organization=org,
                    status__in=["open", "investigating", "escalated"],
                    created_at__gte=since,
                ).count()
            elif rule.metric == "anomaly_z":
                continue  # handled by run_anomaly_detection

            op_fn = _OPERATORS.get(rule.operator)
            if not op_fn or not op_fn(current_value, rule.threshold):
                continue

            # Dedupe: skip if same rule fired in last window
            recent = AlertFiring.objects.filter(
                rule=rule,
                fired_at__gte=since,
                resolved_at__isnull=True,
            ).exists()
            if recent:
                continue

            incident = SecurityIncident.objects.create(
                organization=org,
                title=f"Alert: {rule.name}",
                severity=rule.severity,
                status="open",
                notes=f"Metric {rule.metric}={current_value:.2f} exceeded threshold {rule.threshold}",
            )
            firing = AlertFiring.objects.create(
                rule=rule,
                current_value=current_value,
                linked_incident=incident,
                message=f"{rule.metric}={current_value:.2f} (threshold {rule.operator} {rule.threshold})",
            )

            if rule.playbook_id:
                run = PlaybookRun.objects.create(playbook=rule.playbook, trigger="alert", status="running")
                execute_playbook.delay(run.id)

            logger.info("Alert fired: rule=%s org=%s value=%.2f", rule.id, org.id, current_value)


@shared_task(queue="compute.heavy", bind=True, max_retries=2)
def run_anomaly_detection(self, org_id=None):
    """Compute z-scores and fire anomalies."""
    from auth.models import Organization
    from module2.models import AnomalyRule, PlaybookRun
    from policy.models import EnforcementEvent, SecurityIncident

    orgs = Organization.objects.filter(pk=org_id) if org_id else Organization.objects.filter(is_active=True)
    for org in orgs:
        for rule in AnomalyRule.objects.filter(organization=org, enabled=True):
            since = timezone.now() - timedelta(hours=rule.baseline_window_hours)
            events = EnforcementEvent.objects.filter(organization=org, created_at__gte=since)
            if rule.scope == "agent" and rule.scope_id:
                events = events.filter(agent_id=rule.scope_id)
            elif rule.scope == "model" and rule.scope_id:
                events = events.filter(metadata__model=rule.scope_id)

            bucket_hours = max(1, rule.baseline_window_hours // 24)
            rates = []
            for i in range(24):
                end = since + timedelta(hours=(i + 1) * bucket_hours)
                start = since + timedelta(hours=i * bucket_hours)
                count = events.filter(created_at__gte=start, created_at__lt=end).count()
                rates.append(count / bucket_hours if bucket_hours else count)

            if len(rates) < 2:
                continue
            mean = statistics.mean(rates)
            stdev = statistics.stdev(rates)
            current = events.filter(created_at__gte=timezone.now() - timedelta(hours=1)).count()
            z_score = (current - mean) / stdev if stdev else 0

            if z_score < rule.z_score_threshold:
                continue

            incident = SecurityIncident.objects.create(
                organization=org,
                title=f"Anomaly: {rule.name or rule.metric}",
                severity="high",
                status="open",
                notes=f"Z-score {z_score:.2f} exceeded threshold {rule.z_score_threshold}",
            )
            if rule.playbook_id:
                run = PlaybookRun.objects.create(playbook=rule.playbook, trigger="anomaly", status="running")
                execute_playbook.delay(run.id)

            logger.info("Anomaly detected: rule=%s org=%s z=%.2f", rule.id, org.id, z_score)


@shared_task(queue="policy.compile", bind=True, max_retries=3)
def sync_threat_intel_to_redis(self, org_id):
    """Rebuild Redis threat intel key for an org."""
    from auth.models import Organization
    from module2.models import ThreatIntelEntry

    try:
        org = Organization.objects.get(pk=org_id)
    except Organization.DoesNotExist:
        return

    from django.db.models import Q

    now = timezone.now()
    entries = ThreatIntelEntry.objects.filter(organization=org).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )

    payload = [
        {
            "threat_type": e.threat_type,
            "indicator": e.indicator,
            "owasp_code": e.owasp_code,
            "confidence": e.confidence,
            "auto_block": e.auto_block,
        }
        for e in entries
    ]

    slug = org.slug or str(org.id)
    key = f"{THREAT_INTEL_KEY_PREFIX}{slug}"
    try:
        client = _get_redis_client()
        client.set(key, json.dumps(payload))
        client.publish(THREAT_INTEL_CHANNEL, json.dumps({"org_slug": slug}))
        logger.info("Synced %d threat intel entries to %s", len(payload), key)
    except Exception:
        logger.warning("Failed to sync threat intel to Redis", exc_info=True)
        raise


@shared_task(queue="platform.batch", bind=True, max_retries=2)
def execute_playbook(self, run_id):
    """Execute playbook steps."""
    from module2.models import PlaybookRun

    try:
        run = PlaybookRun.objects.select_related("playbook").get(pk=run_id)
    except PlaybookRun.DoesNotExist:
        return

    results = []
    for step in run.playbook.steps or []:
        action = step.get("action", "")
        params = step.get("params") or {}
        try:
            if action == "notify_webhook":
                import urllib.request
                url = params.get("url", "")
                if url:
                    req = urllib.request.Request(url, data=json.dumps(params.get("body", {})).encode(), method="POST")
                    req.add_header("Content-Type", "application/json")
                    urllib.request.urlopen(req, timeout=10)
                results.append({"action": action, "status": "ok"})
            elif action == "create_incident":
                results.append({"action": action, "status": "skipped", "detail": "incident already created"})
            else:
                results.append({"action": action, "status": "ok", "params": params})
        except Exception as exc:
            results.append({"action": action, "status": "failed", "error": str(exc)})

    run.result = {"steps": results}
    run.status = "success" if all(r.get("status") == "ok" for r in results) else "failed"
    run.finished_at = timezone.now()
    run.save(update_fields=["result", "status", "finished_at"])


@shared_task(queue="compute.heavy")
def evaluate_all_org_alerts():
    """Beat entry: evaluate alerts for all active orgs."""
    from auth.models import Organization
    for org in Organization.objects.filter(is_active=True):
        evaluate_alert_rules.delay(org.id)


@shared_task(queue="default")
def repair_telemetry_metadata():
    """Beat/thread entry: self-heal historical Module 2 telemetry rows."""
    from module2.telemetry_health import maybe_repair_stale_telemetry

    return maybe_repair_stale_telemetry(force=True)


@shared_task(queue="compute.heavy")
def run_all_anomaly_detection():
    """Beat entry: run anomaly detection for all active orgs."""
    from auth.models import Organization
    for org in Organization.objects.filter(is_active=True):
        run_anomaly_detection.delay(org.id)
