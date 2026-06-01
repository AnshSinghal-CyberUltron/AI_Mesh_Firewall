"""
Periodic scan of gateway-written model risk scores → KillSwitch activation.

Gateway writes ``model_risk_score:{org_slug}:{model_name}`` in Redis.
When score >= ModelState.threshold, upsert+activate KillSwitch with audit trail.
"""

from __future__ import annotations

import json
import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def _map_model_state_action(action: str) -> str:
    normalized = (action or "block").strip().lower()
    if normalized == "reroute":
        return "reroute"
    return "disable"


@shared_task(name="isolation.scan_model_risk_scores")
def scan_model_risk_scores_task() -> dict:
    from core.kill_switch_audit import write_kill_switch_audit
    from core.models import KillSwitch, ModelState
    from core.signals import _get_redis_client

    client = _get_redis_client()
    activated = 0
    skipped = 0
    scanned = 0

    for key in client.scan_iter("model_risk_score:*"):
        scanned += 1
        try:
            key_str = key.decode() if isinstance(key, bytes) else str(key)
            parts = key_str.split(":", 2)
            if len(parts) != 3:
                skipped += 1
                continue
            _, org_slug, model_name = parts
            raw_score = client.get(key)
            if raw_score is None:
                skipped += 1
                continue
            score = float(raw_score.decode() if isinstance(raw_score, bytes) else raw_score)
        except (ValueError, TypeError):
            skipped += 1
            continue

        ms = (
            ModelState.objects.select_related("organization")
            .filter(organization__slug=org_slug, model_name=model_name)
            .first()
        )
        if ms is None:
            skipped += 1
            continue

        threshold = float(ms.threshold)
        if score < threshold:
            skipped += 1
            continue

        existing = KillSwitch.objects.filter(
            organization=ms.organization,
            model_name=model_name,
            api_key_prefix="",
            is_active=True,
        ).first()
        if existing is not None:
            skipped += 1
            continue

        action = _map_model_state_action(ms.action)
        reason = f"auto_risk score={score:.1f} threshold={threshold:.1f}"
        ks, _created = KillSwitch.objects.update_or_create(
            organization=ms.organization,
            model_name=model_name,
            api_key_prefix="",
            defaults={
                "action": action,
                "fallback_model": ms.fallback_model if action == "reroute" else "",
                "reason": reason,
                "is_active": True,
                "activated_at": timezone.now(),
            },
        )
        if not ks.is_active:
            ks.is_active = True
            ks.activated_at = timezone.now()
            ks.save(update_fields=["is_active", "activated_at", "updated_at"])

        write_kill_switch_audit(
            instance=ks,
            event="kill_switch_activated",
            triggered_by="auto_risk_job",
            trigger_source="auto_risk",
        )
        activated += 1
        logger.warning(
            "Auto-risk activated KillSwitch org=%s model=%s score=%.1f",
            org_slug,
            model_name,
            score,
        )

    return {"scanned": scanned, "activated": activated, "skipped": skipped}
