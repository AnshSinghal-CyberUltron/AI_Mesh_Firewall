"""
Notification APIs: list and mark-as-read for persisted escalation/resolution notifications.
"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from policy.models import Notification


def _entitled_notifications(user):
    """The caller's bells, limited to events of the caller's own org (#120, #122).

    A bell is readable only while its recipient is in the event's org. Develop fanned
    escalation and isolation bells out to every superuser of every org (#120 defects 3-4);
    those rows stay in the database after the fan-out fix, and the list reads the event's
    live ``incident_status``, so without this filter they would keep reporting another
    org's incident. The same filter covers a user who moved org or lost a role. It applies
    to staff too: staff reach other orgs through the audited incident and WebSocket paths,
    not through bells. A caller without an org has no bells. Legacy rows are removed by
    ``manage.py purge_cross_org_notifications``.
    """
    from auth.audit import actor_org_id

    org_id = actor_org_id(user)
    if org_id is None:
        return Notification.objects.none()
    return Notification.objects.filter(recipient_id=user.id, enforcement_event__organization_id=org_id)


class NotificationsListView(APIView):
    """
    GET /api/notifications/
    Returns the current user's notifications for events of their own org
    (recipient_id and enforcement_event.organization_id; see _entitled_notifications).
    Query params:
      - unread_only=true  (default: false — returns all)
      - limit=N           (default: 50, max: 200)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        unread_only = request.query_params.get("unread_only", "false").lower() == "true"
        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50  # was a generic 400 from the exception handler
        limit = max(1, min(limit, 200))  # a negative limit was a 500 (negative slice: AssertionError)

        qs = _entitled_notifications(request.user).select_related("enforcement_event")
        if unread_only:
            qs = qs.filter(read=False)
        qs = qs[:limit]

        items = []
        for n in qs:
            ev = n.enforcement_event
            meta = ev.metadata or {} if ev else {}
            category = meta.get("threat_category") or "Security event"
            items.append({
                "id": n.id,
                "type": n.type,
                "message": n.message,
                "enforcement_event_id": str(ev.id) if ev else None,
                "incident_title": category,
                "incident_status": ev.incident_status if ev else None,
                "read": n.read,
                "created_at": n.created_at.isoformat(),
            })

        return Response(items)


class NotificationMarkReadView(APIView):
    """
    POST /api/notifications/{pk}/read/
    Marks a single notification as read (must belong to the current user and to an
    event of their org; anything else is 404, the same as a missing id).

    POST /api/notifications/mark-all-read/
    Marks all of the current user's entitled notifications as read.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        if pk is not None:
            updated = _entitled_notifications(request.user).filter(pk=pk).update(read=True)
            if not updated:
                return Response({"detail": "Notification not found."}, status=404)
            return Response({"detail": "Marked as read."})
        # mark-all-read path (pk is None)
        _entitled_notifications(request.user).filter(read=False).update(read=True)
        return Response({"detail": "All notifications marked as read."})
