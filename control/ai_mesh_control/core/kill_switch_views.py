"""
API views for the KillSwitch emergency model disable/reroute system.

Endpoints:
    GET    /api/kill-switches/         -- List all kill-switches
    POST   /api/kill-switches/         -- Create a new kill-switch entry
    GET    /api/kill-switches/{id}/    -- Retrieve a kill-switch
    PUT    /api/kill-switches/{id}/    -- Update a kill-switch
    DELETE /api/kill-switches/{id}/    -- Delete a kill-switch
    POST   /api/kill-switches/{id}/activate/   -- Activate the kill-switch
    POST   /api/kill-switches/{id}/deactivate/ -- Deactivate the kill-switch
"""

import logging

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import KillSwitch
from core.serializers import (
    KillSwitchActivateSerializer,
    KillSwitchCreateSerializer,
    KillSwitchSerializer,
)

logger = logging.getLogger(__name__)


class KillSwitchViewSet(viewsets.ModelViewSet):
    """
    CRUD + activate/deactivate for emergency kill-switches.

    Each kill-switch targets a specific model name (or ``__global__``
    for all models). On activation, the gateway immediately rejects
    or reroutes requests for the targeted model.
    """

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        org = getattr(getattr(user, "profile", None), "organization", None)
        if org:
            return KillSwitch.objects.filter(organization=org).order_by("-updated_at")
        return KillSwitch.objects.none()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            KillSwitchSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_create(self, serializer):
        org = self.request.user.profile.organization
        serializer.save(organization=org)

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return KillSwitchCreateSerializer
        if self.action in ("activate", "deactivate"):
            return KillSwitchActivateSerializer
        return KillSwitchSerializer

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        """Activate the kill-switch. Sets is_active=True and timestamps."""
        instance = self.get_object()
        serializer = KillSwitchActivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reason = serializer.validated_data.get("reason", "")
        if reason:
            instance.reason = reason

        instance.is_active = True
        instance.activated_at = timezone.now()
        instance.activated_by = request.user
        instance.save()

        logger.warning(
            "KillSwitch ACTIVATED: model=%s action=%s by=%s reason=%s",
            instance.model_name,
            instance.action,
            request.user.username,
            instance.reason,
        )
        return Response(
            KillSwitchSerializer(instance).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        """Deactivate the kill-switch. Clears is_active and activated_at."""
        instance = self.get_object()

        instance.is_active = False
        instance.activated_at = None
        instance.save()

        logger.info(
            "KillSwitch deactivated: model=%s by=%s",
            instance.model_name,
            request.user.username,
        )
        return Response(
            KillSwitchSerializer(instance).data,
            status=status.HTTP_200_OK,
        )
