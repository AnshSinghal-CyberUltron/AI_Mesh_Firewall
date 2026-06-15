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

from core.admin_views import IsAdminOrSuperuser
from core.kill_switch_audit import write_kill_switch_audit
from core.model_state_bootstrap import (
    canonicalize_model_name_safe,
    is_reserved_guard_model_name,
)
from core.models import KillSwitch, ModelState, platform_guard_model_names
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

    # Mutating a kill-switch is an emergency containment action: restrict it to
    # org admins / superusers. Listing/retrieval stays open to any authenticated
    # org member (read-only). The queryset is already org-scoped below.
    _MUTATING_ACTIONS = ("create", "update", "partial_update", "destroy", "activate", "deactivate")

    def get_permissions(self):
        if getattr(self, "action", None) in self._MUTATING_ACTIONS:
            return [IsAuthenticated(), IsAdminOrSuperuser()]
        return [IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        org = getattr(getattr(user, "profile", None), "organization", None)
        if org:
            return KillSwitch.objects.filter(organization=org).order_by("-updated_at")
        return KillSwitch.objects.none()

    def list(self, request, *args, **kwargs):
        """List KillSwitch rows, then reflect ModelState-isolated models that
        have no KillSwitch as read-only synthetic rows (``source='model_state'``).

        ModelState isolation (Risk Monitor) and KillSwitch (this table) are two
        independent kill subsystems; surfacing risk-monitor isolations here keeps
        the two surfaces consistent. Synthetic rows carry ``id=null`` so the
        frontend renders them without management controls.
        """
        response = super().list(request, *args, **kwargs)
        org = getattr(getattr(request.user, "profile", None), "organization", None)
        if org is None:
            return response

        data = response.data
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict) and isinstance(data.get("results"), list):
            rows = data["results"]
        else:
            return response

        existing_names = {r.get("model_name") for r in rows if isinstance(r, dict)}
        # De-leak raw guard/upstream model ids on the kill-switch rows themselves.
        # KillSwitch actions are keyed on row id (not model_name), so canonicalizing
        # the DISPLAYED name is safe and keeps edit/activate/delete working.
        for r in rows:
            if isinstance(r, dict) and r.get("model_name"):
                r["model_name"] = canonicalize_model_name_safe(r["model_name"])
        guard = set(platform_guard_model_names())
        isolated = ModelState.objects.filter(
            organization=org, status="isolated"
        ).exclude(model_name__in=list(guard))

        synthetic = []
        for st in isolated:
            if st.model_name in existing_names or is_reserved_guard_model_name(st.model_name):
                continue
            synthetic.append(
                {
                    "id": None,
                    "model_name": st.model_name,
                    "api_key_prefix": "",
                    "is_active": True,
                    "action": st.action or "block",
                    "fallback_model": st.fallback_model or "",
                    "reason": st.isolation_reason or "Model isolated via Risk Monitor",
                    "activated_by_username": "risk-monitor",
                    "activated_at": st.isolated_at.isoformat() if st.isolated_at else None,
                    "source": "model_state",
                }
            )

        if synthetic:
            combined = list(rows) + synthetic
            if isinstance(data, list):
                response.data = combined
            else:
                data["results"] = combined
                response.data = data
        return response

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
