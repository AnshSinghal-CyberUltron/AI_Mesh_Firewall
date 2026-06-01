"""Alertmanager webhook receiver — activates KillSwitch from firing alerts."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from auth.models import Organization
from core.kill_switch_audit import write_kill_switch_audit
from core.models import KillSwitch

logger = logging.getLogger(__name__)


def _webhook_secret_ok(request) -> bool:
    expected = str(getattr(settings, "ALERTMANAGER_WEBHOOK_SECRET", "") or "").strip()
    if not expected:
        logger.error("ALERTMANAGER_WEBHOOK_SECRET is not configured")
        return False
    provided = (
        request.headers.get("X-Alertmanager-Secret")
        or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        or request.GET.get("secret", "")
    )
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def _map_action(raw: str) -> str:
    action = (raw or "disable").strip().lower()
    return action if action in ("disable", "reroute") else "disable"


@csrf_exempt
@require_POST
@api_view(["POST"])
@permission_classes([AllowAny])
def alertmanager_webhook_view(request):
    """
    POST /api/webhooks/alertmanager/

    Accepts Alertmanager v4 webhook JSON. Maps labels to KillSwitch upsert+activate.
    """
    if not _webhook_secret_ok(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    alerts = payload.get("alerts") or []
    if not isinstance(alerts, list):
        return JsonResponse({"error": "alerts must be a list"}, status=400)

    processed = 0
    skipped = 0
    errors: list[str] = []

    for alert in alerts:
        if not isinstance(alert, dict):
            skipped += 1
            continue
        status = str(alert.get("status") or "").lower()
        if status and status != "firing":
            skipped += 1
            continue

        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        org_slug = str(labels.get("org_slug") or labels.get("organization") or "").strip()
        model_name = str(labels.get("model_name") or labels.get("model") or "").strip()
        if not org_slug or not model_name:
            skipped += 1
            continue

        org = Organization.objects.filter(slug=org_slug).first()
        if org is None:
            errors.append(f"unknown org_slug={org_slug}")
            continue

        action = _map_action(str(labels.get("action") or "disable"))
        fallback_model = str(labels.get("fallback_model") or "").strip()
        reason = str(
            annotations.get("summary")
            or annotations.get("description")
            or labels.get("reason")
            or "alertmanager"
        ).strip()
        api_key_prefix = str(labels.get("api_key_prefix") or "").strip()
        fingerprint = str(alert.get("fingerprint") or "").strip()

        defaults = {
            "action": action,
            "fallback_model": fallback_model if action == "reroute" else "",
            "reason": reason,
            "is_active": True,
            "activated_at": timezone.now(),
        }
        ks, created = KillSwitch.objects.update_or_create(
            organization=org,
            model_name=model_name,
            api_key_prefix=api_key_prefix,
            defaults=defaults,
        )
        if not ks.is_active:
            ks.is_active = True
            ks.activated_at = timezone.now()
            ks.save(update_fields=["is_active", "activated_at", "updated_at"])

        write_kill_switch_audit(
            instance=ks,
            event="kill_switch_activated",
            triggered_by=fingerprint or "alertmanager",
            trigger_source="alertmanager",
        )
        processed += 1
        logger.warning(
            "Alertmanager activated KillSwitch org=%s model=%s action=%s",
            org_slug,
            model_name,
            action,
        )

    return JsonResponse(
        {
            "status": "ok",
            "processed": processed,
            "skipped": skipped,
            "errors": errors,
        },
        status=200,
    )
