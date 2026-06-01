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

from core.kill_switch_audit import write_kill_switch_audit
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
        warning = serializer.validated_data.pop("_model_name_warning", None)
        self.perform_create(serializer)
        payload = KillSwitchSerializer(serializer.instance).data
        if warning:
            payload["warning"] = warning
        return Response(payload, status=status.HTTP_201_CREATED)

    def perform_create(self, serializer):
        org = self.request.user.profile.organization
        instance = serializer.save(organization=org)
        if instance.is_active:
            write_kill_switch_audit(
                instance=instance,
                event="kill_switch_activated",
                triggered_by=self.request.user.email or self.request.user.username,
                trigger_source="create",
            )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

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

        org = instance.organization
        if org is not None:
            write_kill_switch_audit(
                instance=instance,
                event="kill_switch_activated",
                triggered_by=request.user.email or request.user.username,
                trigger_source="manual",
            )

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

        org = instance.organization
        if org is not None:
            write_kill_switch_audit(
                instance=instance,
                event="kill_switch_deactivated",
                triggered_by=request.user.email or request.user.username,
                trigger_source="manual",
            )

        logger.info(
            "KillSwitch deactivated: model=%s by=%s",
            instance.model_name,
            request.user.username,
        )
        return Response(
            KillSwitchSerializer(instance).data,
            status=status.HTTP_200_OK,
        )
