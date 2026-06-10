"""
API views for ModelState — real-time per-model status and risk scoring.

Endpoints:
    GET    /api/models/status/                -- List all model states
    GET    /api/models/status/{model_name}/   -- Get specific model state
    PATCH  /api/models/status/{model_name}/   -- Update threshold/action config
    POST   /api/models/isolate/               -- Manually isolate a model
    POST   /api/models/recover/{model_name}/  -- Manually recover a model
    GET    /api/models/audit-log/             -- List kill-switch audit logs
"""

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.model_state_bootstrap import (
    ensure_model_states_for_org,
    merge_model_states_with_configs,
)
from core.models import (
    KillSwitchAuditLog,
    ModelState,
    is_platform_managed_llm_model_name,
)
from core.serializers import (
    KillSwitchAuditLogSerializer,
    ModelIsolateSerializer,
    ModelStateSerializer,
    ModelStateUpdateSerializer,
)

logger = logging.getLogger(__name__)


def _get_org(request):
    return getattr(getattr(request.user, "profile", None), "organization", None)


class ModelStatusListView(APIView):
    """List all model states for the current org."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _get_org(request)
        if not org:
            return Response([], status=status.HTTP_200_OK)
        states = ModelState.objects.filter(organization=org)
        return Response(merge_model_states_with_configs(org, states))


class ModelStatusSyncView(APIView):
    """POST /api/models/sync/ — bootstrap ModelState from active LLM configs."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        org = _get_org(request)
        if not org:
            return Response(
                {"error": "No organization"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        created, active = ensure_model_states_for_org(org)
        states = ModelState.objects.filter(organization=org)
        return Response(
            {
                "created": created,
                "active_configs": active,
                "models": merge_model_states_with_configs(org, states),
            }
        )


class ModelStatusDetailView(APIView):
    """Get or update a specific model's state."""

    permission_classes = [IsAuthenticated]

    def get(self, request, model_name):
        org = _get_org(request)
        if not org:
            return Response({"error": "No organization"}, status=status.HTTP_404_NOT_FOUND)
        try:
            state = ModelState.objects.get(organization=org, model_name=model_name)
        except ModelState.DoesNotExist:
            return Response({"error": f"No state for model '{model_name}'"}, status=status.HTTP_404_NOT_FOUND)
        return Response(ModelStateSerializer(state).data)

    def patch(self, request, model_name):
        org = _get_org(request)
        if not org:
            return Response({"error": "No organization"}, status=status.HTTP_404_NOT_FOUND)

        if is_platform_managed_llm_model_name(model_name):
            return Response(
                {"error": "ZeroShield guard models are platform-managed and cannot be isolated."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        state, created = ModelState.objects.get_or_create(
            organization=org, model_name=model_name,
        )

        serializer = ModelStateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        for field in ("threshold", "action", "fallback_model", "cooldown_seconds"):
            if field in serializer.validated_data:
                setattr(state, field, serializer.validated_data[field])
        state.save()

        logger.info(
            "ModelState config updated: model=%s org=%s changes=%s by=%s",
            model_name, org.slug, serializer.validated_data, request.user.email,
        )
        return Response(ModelStateSerializer(state).data)


class ModelIsolateView(APIView):
    """Manually isolate a model — immediate effect via Redis sync."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        org = _get_org(request)
        if not org:
            return Response({"error": "No organization"}, status=status.HTTP_400_BAD_REQUEST)

        serializer = ModelIsolateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if is_platform_managed_llm_model_name(data["model_name"]):
            return Response(
                {"error": "ZeroShield guard models are platform-managed and cannot be isolated."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        state, _ = ModelState.objects.get_or_create(
            organization=org, model_name=data["model_name"],
        )

        now = timezone.now()
        cooldown = data.get("cooldown_seconds", 300)
        from datetime import timedelta

        state.status = "isolated"
        state.action = data.get("action", "block")
        state.fallback_model = data.get("fallback_model", "")
        state.isolation_reason = data.get("reason", "Manual isolation")
        state.isolated_at = now
        state.isolated_until = now + timedelta(seconds=cooldown)
        state.cooldown_seconds = cooldown
        state.save()

        # Create audit log
        KillSwitchAuditLog.objects.create(
            organization=org,
            event="model_isolated",
            model_name=data["model_name"],
            risk_score=state.risk_score,
            threshold=state.threshold,
            action=state.action,
            reason=state.isolation_reason,
            triggered_by=request.user.email,
            metadata={"source": "manual", "cooldown_seconds": cooldown},
        )

        logger.warning(
            "MODEL ISOLATED: model=%s org=%s action=%s reason=%s by=%s",
            data["model_name"], org.slug, state.action,
            state.isolation_reason, request.user.email,
        )
        return Response(ModelStateSerializer(state).data, status=status.HTTP_200_OK)


class ModelRecoverView(APIView):
    """Manually recover a model from isolation."""

    permission_classes = [IsAuthenticated]

    def post(self, request, model_name):
        org = _get_org(request)
        if not org:
            return Response({"error": "No organization"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            state = ModelState.objects.get(organization=org, model_name=model_name)
        except ModelState.DoesNotExist:
            return Response({"error": f"No state for model '{model_name}'"}, status=status.HTTP_404_NOT_FOUND)

        state.status = "active"
        state.isolation_reason = ""
        state.isolated_at = None
        state.isolated_until = None
        state.save()

        # Create audit log
        KillSwitchAuditLog.objects.create(
            organization=org,
            event="model_recovered",
            model_name=model_name,
            risk_score=state.risk_score,
            threshold=state.threshold,
            action="recover",
            reason="Manual recovery",
            triggered_by=request.user.email,
            metadata={"source": "manual"},
        )

        logger.info(
            "MODEL RECOVERED: model=%s org=%s by=%s",
            model_name, org.slug, request.user.email,
        )
        return Response(ModelStateSerializer(state).data)


class KillSwitchAuditLogView(APIView):
    """List kill-switch audit logs for the current org."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _get_org(request)
        if not org:
            return Response([], status=status.HTTP_200_OK)

        limit = min(int(request.query_params.get("limit", 50)), 200)
        model_name = request.query_params.get("model", "")

        qs = KillSwitchAuditLog.objects.filter(organization=org)
        if model_name:
            qs = qs.filter(model_name=model_name)
        logs = qs[:limit]
        return Response(KillSwitchAuditLogSerializer(logs, many=True).data)
