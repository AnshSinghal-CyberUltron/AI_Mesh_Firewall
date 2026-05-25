"""
Notification APIs: list and mark-as-read for persisted escalation/resolution notifications.
"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from policy.models import Notification


class NotificationsListView(APIView):
    """
    GET /api/notifications/
    Returns notifications for the current user (filtered by recipient_id).
    Query params:
      - unread_only=true  (default: false — returns all)
      - limit=N           (default: 50, max: 200)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        unread_only = request.query_params.get("unread_only", "false").lower() == "true"
        limit = min(int(request.query_params.get("limit", 50)), 200)

        qs = Notification.objects.filter(recipient_id=request.user.id).select_related("enforcement_event")
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
    Marks a single notification as read (must belong to the current user).

    POST /api/notifications/mark-all-read/
    Marks all notifications for the current user as read.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        if pk is not None:
            updated = Notification.objects.filter(pk=pk, recipient_id=request.user.id).update(read=True)
            if not updated:
                return Response({"detail": "Notification not found."}, status=404)
            return Response({"detail": "Marked as read."})
        # mark-all-read path (pk is None)
        Notification.objects.filter(recipient_id=request.user.id, read=False).update(read=True)
        return Response({"detail": "All notifications marked as read."})
