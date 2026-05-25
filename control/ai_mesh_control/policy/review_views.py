"""API views for Human Review Queue and Security Incidents."""

import logging

from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from policy.models import HumanReviewItem, SecurityIncident

logger = logging.getLogger(__name__)


class HumanReviewSerializer(serializers.ModelSerializer):
    event_action = serializers.CharField(source="enforcement_event.action", read_only=True)
    event_metadata = serializers.JSONField(source="enforcement_event.metadata", read_only=True)
    reviewer_username = serializers.SerializerMethodField()

    class Meta:
        model = HumanReviewItem
        fields = (
            "id", "enforcement_event_id", "organization_id",
            "status", "reviewer", "reviewer_username", "notes",
            "created_at", "reviewed_at",
            "event_action", "event_metadata",
        )
        read_only_fields = ("id", "enforcement_event_id", "organization_id", "created_at", "reviewed_at")

    def get_reviewer_username(self, obj):
        return obj.reviewer.username if obj.reviewer else None


class ReviewQueueListView(APIView):
    """GET /api/security/review-queue/ - list pending reviews for the user's org."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = getattr(getattr(request.user, "profile", None), "organization", None)
        qs = HumanReviewItem.objects.filter(organization=org) if org else HumanReviewItem.objects.none()
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        serializer = HumanReviewSerializer(qs[:100], many=True)
        return Response({"count": qs.count(), "results": serializer.data})


class ReviewApproveView(APIView):
    """POST /api/security/review-queue/{id}/approve/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        org = getattr(getattr(request.user, "profile", None), "organization", None)
        try:
            item = HumanReviewItem.objects.get(pk=pk, organization=org)
        except HumanReviewItem.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        item.status = "approved"
        item.reviewer = request.user
        item.reviewed_at = timezone.now()
        item.notes = request.data.get("notes", item.notes)
        item.save()
        return Response(HumanReviewSerializer(item).data)


class ReviewRejectView(APIView):
    """POST /api/security/review-queue/{id}/reject/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        org = getattr(getattr(request.user, "profile", None), "organization", None)
        try:
            item = HumanReviewItem.objects.get(pk=pk, organization=org)
        except HumanReviewItem.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        item.status = "rejected"
        item.reviewer = request.user
        item.reviewed_at = timezone.now()
        item.notes = request.data.get("notes", item.notes)
        item.save()
        return Response(HumanReviewSerializer(item).data)


class SecurityIncidentSerializer(serializers.ModelSerializer):
    assigned_to_username = serializers.SerializerMethodField()

    class Meta:
        model = SecurityIncident
        fields = (
            "id", "organization_id", "enforcement_event_id",
            "title", "severity", "status", "assigned_to",
            "assigned_to_username", "notes",
            "created_at", "updated_at", "resolved_at",
        )
        read_only_fields = ("id", "organization_id", "created_at", "updated_at")

    def get_assigned_to_username(self, obj):
        return obj.assigned_to.username if obj.assigned_to else None


class SecurityIncidentListView(APIView):
    """GET /api/security/incidents/ - list incidents for the org."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = getattr(getattr(request.user, "profile", None), "organization", None)
        qs = SecurityIncident.objects.filter(organization=org) if org else SecurityIncident.objects.none()
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        serializer = SecurityIncidentSerializer(qs[:100], many=True)
        return Response({"count": qs.count(), "results": serializer.data})


class SecurityIncidentEscalateView(APIView):
    """POST /api/security/incidents/{id}/escalate-incident/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        org = getattr(getattr(request.user, "profile", None), "organization", None)
        try:
            incident = SecurityIncident.objects.get(pk=pk, organization=org)
        except SecurityIncident.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        incident.status = "escalated"
        incident.notes = request.data.get("notes", incident.notes)
        incident.save()
        return Response(SecurityIncidentSerializer(incident).data)


class SecurityIncidentResolveView(APIView):
    """POST /api/security/incidents/{id}/resolve-incident/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        org = getattr(getattr(request.user, "profile", None), "organization", None)
        try:
            incident = SecurityIncident.objects.get(pk=pk, organization=org)
        except SecurityIncident.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        incident.status = "resolved"
        incident.resolved_at = timezone.now()
        incident.notes = request.data.get("notes", incident.notes)
        incident.save()
        return Response(SecurityIncidentSerializer(incident).data)
