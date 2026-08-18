"""Create Header-bell notifications for Module 1.6 isolation events."""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db.models import Q

logger = logging.getLogger(__name__)

_ISOLATION_EVENT_TYPES = frozenset({"kill_switch", "model_isolation", "circuit_breaker"})
_NOTIFY_ACTIONS = frozenset({"block", "disable", "reroute", "isolate", "isolated", "monitor", "alert"})


def create_isolation_bell_notifications_for_events(events) -> int:
    """Persist Notification(type=escalation) rows for org admins from EnforcementEvents.

    Returns number of Notification rows created. Safe to call with empty list.
    Reuses type=escalation so no migration is required; message is prefixed
    with ``[Isolation]`` so the bell is distinguishable from manual escalate.
    """
    if not events:
        return 0

    from policy.models import Notification

    User = get_user_model()
    created = 0
    for ev in events:
        try:
            meta = ev.metadata if isinstance(ev.metadata, dict) else {}
            event_type = (
                meta.get("event_type")
                or meta.get("threat_type")
                or ""
            )
            is_iso = bool(meta.get("is_isolation_event")) or event_type in _ISOLATION_EVENT_TYPES
            if not is_iso:
                continue
            action = (getattr(ev, "action", None) or meta.get("action") or "").lower()
            if action and action not in _NOTIFY_ACTIONS:
                # Still notify for isolation-tagged events with unusual actions
                pass

            org_id = getattr(ev, "organization_id", None)
            model = meta.get("model") or meta.get("model_name") or ""
            message = (
                f"[Isolation] {event_type or 'isolation'} action={action or 'n/a'}"
                + (f" model={model}" if model else "")
                + f" (event #{ev.id})"
            )

            admin_qs = User.objects.filter(
                Q(is_superuser=True) | Q(profile__roles__name="platform_admin")
            ).distinct()
            if org_id is not None:
                admin_qs = admin_qs.filter(
                    Q(profile__organization_id=org_id) | Q(is_superuser=True)
                )
            admin_ids = list(admin_qs.values_list("id", flat=True))
            if not admin_ids:
                continue

            existing = set(
                Notification.objects.filter(
                    enforcement_event=ev, type="escalation"
                ).values_list("recipient_id", flat=True)
            )
            rows = [
                Notification(
                    type="escalation",
                    enforcement_event=ev,
                    recipient_id=admin_id,
                    message=message,
                )
                for admin_id in admin_ids
                if admin_id not in existing
            ]
            if rows:
                Notification.objects.bulk_create(rows)
                created += len(rows)
                try:
                    from ws.notify import send_enforcement_notification

                    send_enforcement_notification(
                        {
                            "type": "escalation_event",
                            "incident_id": str(ev.id),
                            "message": message,
                            "organization_id": org_id,
                            "isolation": True,
                            "event_type": event_type,
                        }
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("isolation bell WS broadcast failed", exc_info=True)
        except Exception:  # noqa: BLE001 — never break telemetry drain
            logger.warning("create_isolation_bell_notifications failed for event", exc_info=True)
    return created


def notify_isolation_from_control(
    *,
    organization,
    event_type: str,
    model_name: str,
    action: str,
    reason: str = "",
    triggered_by: str = "",
) -> int:
    """Create a synthetic EnforcementEvent + bell notifications from control-plane actions."""
    from policy.models import EnforcementEvent

    if organization is None:
        return 0
    # Map to EnforcementEvent.action max_length=16
    ev_action = (action or "block")[:16]
    if ev_action == "disable":
        ev_action = "block"
    if ev_action == "isolate":
        ev_action = "block"
    meta = {
        "event_type": event_type,
        "threat_type": event_type,
        "is_isolation_event": True,
        "model": model_name,
        "action": action,
        "reason": reason,
        "triggered_by": triggered_by,
        "module_id": "1.6",
    }
    ev = EnforcementEvent.objects.create(
        organization=organization,
        action=ev_action,
        event_class="isolation",
        metadata=meta,
        policy=None,
        rule=None,
    )
    return create_isolation_bell_notifications_for_events([ev])
