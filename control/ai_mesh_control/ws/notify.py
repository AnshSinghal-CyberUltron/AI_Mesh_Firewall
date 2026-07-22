"""
Send real-time notifications to WebSocket clients (e.g. on enforcement events).
Call from sync code (Django views). Uses channel layer group_send.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .consumers import notification_group_for_org

logger = logging.getLogger(__name__)


def _resolve_org_id(payload: dict, organization_id: int | None = None) -> int | None:
    if organization_id is not None:
        try:
            resolved = int(organization_id)
            return resolved if resolved > 0 else None
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    org_id = payload.get("organization_id")
    if org_id is None:
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        org_id = meta.get("organization_id")
    try:
        if org_id is None:
            return None
        resolved = int(org_id)
        return resolved if resolved > 0 else None
    except (TypeError, ValueError):
        return None


def send_enforcement_notification(payload: dict, organization_id: int | None = None) -> None:
    """
    Broadcast an enforcement event to all connected notification WebSocket clients.
    payload: JSON-serializable dict, e.g. { "type": "enforcement_event", "id", "action", "timestamp", "severity", "category", "source", "metadata", ... }.
    """
    org_id = _resolve_org_id(payload, organization_id=organization_id)
    if org_id is None:
        # Hard fail-closed for tenant isolation: do not broadcast unscoped payloads.
        return

    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    message = {"type": "notification_message", "message": payload}
    group_name = notification_group_for_org(org_id)
    try:
        async_to_sync(channel_layer.group_send)(group_name, message)
    except Exception:
        # #42: keep the caller (enforcement decision / telemetry drain) resilient —
        # a websocket-broadcast failure must NOT propagate. But the old bare
        # `return` swallowed it SILENTLY, so a broken channel layer would drop
        # EVERY real-time enforcement alert with zero operator visibility. Log it
        # (fail-open on delivery, but observably) so dropped alerts are detectable.
        logger.warning(
            "failed to broadcast enforcement notification for org=%s (event dropped): ",
            org_id,
            exc_info=True,
        )
        return
