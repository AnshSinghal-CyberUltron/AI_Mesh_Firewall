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

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.admin_views import IsAdminOrSuperuser
from core.model_state_bootstrap import (
    canonicalize_model_name_safe,
    ensure_model_states_for_org,
    is_reserved_guard_model_name,
    merge_model_states_with_configs,
)
from core.models import (
    KillSwitch,
    KillSwitchAuditLog,
    ModelState,
    is_platform_managed_llm_model_name,
    platform_guard_model_names,
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


def _overlay_active_kill_switches(org, merged):
    """Reflect active KillSwitch rows into the model-state list.

    A model killed via the KillSwitch subsystem (separate table from ModelState)
    would otherwise never appear in the Risk Monitor, while it DOES appear in
    Kill-Switch Management — the exact cross-surface inconsistency this resolves.
    For a kill-switched model already present, annotate it; otherwise append a
    read-only synthetic row tagged ``source='kill_switch'`` so the frontend
    renders it without offering ModelState-only isolate/recover controls.
    """
    guard = set(platform_guard_model_names())
    active = KillSwitch.objects.filter(organization=org, is_active=True).exclude(
        model_name=KillSwitch.SCOPE_GLOBAL
    )
    by_name = {row["model_name"]: row for row in merged}
    for ks in active:
        if (
            ks.model_name in guard
            or is_platform_managed_llm_model_name(ks.model_name)
            or is_reserved_guard_model_name(ks.model_name)
        ):
            continue
        existing = by_name.get(ks.model_name)
        if existing is not None:
            existing["kill_switch_active"] = True
            existing["kill_switch_action"] = ks.action
            existing["kill_switch_reason"] = ks.reason or ""
            continue
        merged.append(
            {
                "id": None,
                "model_name": ks.model_name,
                "status": "isolated",
                "risk_score": 0.0,
                "threshold": 80.0,
                "action": ks.action,
                "fallback_model": ks.fallback_model or "",
                "isolation_reason": ks.reason or "Kill-switch active",
                "isolated_at": ks.activated_at.isoformat() if ks.activated_at else None,
                "isolated_until": None,
                "cooldown_seconds": 300,
                "last_updated": None,
                "created_at": None,
                "organization_slug": org.slug,
                "gateway_model_hint": ks.model_name,
                "is_bootstrapped": True,
                "source": "kill_switch",
                "kill_switch_active": True,
                "kill_switch_action": ks.action,
                "kill_switch_reason": ks.reason or "",
            }
        )
    merged.sort(key=lambda row: row["model_name"])
    return merged


class ModelStatusListView(APIView):
    """List all model states for the current org."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _get_org(request)
        if not org:
            return Response([], status=status.HTTP_200_OK)
        # Platform/guard model states must NEVER surface in the tenant
        # model-isolation UI (the user-facing platform name is "ZeroShield Model"
        # only — its raw guard/adjudicator ids must not leak). Filter server-side
        # (authoritative) rather than relying on the frontend allowlist filter.
        states = ModelState.objects.filter(organization=org).exclude(
            model_name__in=list(platform_guard_model_names())
        )
        merged = merge_model_states_with_configs(org, states)
        merged = _overlay_active_kill_switches(org, merged)
        return Response(merged)


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
        # Changing isolation/threshold config is an admin-only containment action.
        if not IsAdminOrSuperuser().has_permission(request, self):
            return Response(
                {"error": "Admin role required to change model isolation state."},
                status=status.HTTP_403_FORBIDDEN,
            )
        org = _get_org(request)
        if not org:
            return Response({"error": "No organization"}, status=status.HTTP_404_NOT_FOUND)

        # R14: reject a non-dict JSON body up front (the DRF serializer +
        # downstream code assume a mapping; a bare list/string would 500).
        if not isinstance(request.data, dict):
            return Response(
                {"error": "Request body must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
        # M-20 root cause: the DRF path called .save() without ever invoking the
        # model's clean(), so ModelState.clean() (self-loop guard) never ran.
        # full_clean() enforces model-level invariants before persisting.
        try:
            state.full_clean()
        except DjangoValidationError as exc:
            raise DRFValidationError(getattr(exc, "message_dict", exc.messages))
        state.save()

        logger.info(
            "ModelState config updated: model=%s org=%s changes=%s by=%s",
            model_name, org.slug, serializer.validated_data, request.user.email,
        )
        return Response(ModelStateSerializer(state).data)


class ModelIsolateView(APIView):
    """Manually isolate a model — immediate effect via Redis sync."""

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]

    def post(self, request):
        org = _get_org(request)
        if not org:
            return Response({"error": "No organization"}, status=status.HTTP_400_BAD_REQUEST)

        # R14: a non-dict JSON body (bare list/string/number) reaches the DRF
        # serializer and helpers that assume a mapping, producing an unhandled
        # 500. Reject it cleanly with a 400 instead.
        if not isinstance(request.data, dict):
            return Response(
                {"error": "Request body must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Pass request context so the serializer can reject a platform guard /
        # nonexistent / cross-org fallback_model (reroute eligibility check).
        serializer = ModelIsolateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Global scope is disabled: isolation always targets a specific model.
        if (data["model_name"] or "").strip() == "__global__":
            return Response(
                {"error": "Global ('__global__') isolation is disabled. Isolate a specific model."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
        # M-20 root cause: enforce model-level invariants (e.g. fallback_model
        # self-loop) on the DRF .save() path, which never invoked clean().
        try:
            state.full_clean()
        except DjangoValidationError as exc:
            raise DRFValidationError(getattr(exc, "message_dict", exc.messages))
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

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]

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
        # Clear the reroute config set during isolation. Leaving a stale
        # action='reroute' + fallback_model on a now-active model means a GET
        # reports action=reroute with a possibly-invalid fallback, and on
        # re-isolation the stale config silently re-applies. Reset to the
        # governance default ('block') and drop the fallback target.
        state.action = "block"
        state.fallback_model = ""
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

        # Platform/guard model ids must NEVER leak into the tenant audit-log
        # surface (user-facing name is "ZeroShield Model" only). Exclude their
        # raw model_name rows server-side (authoritative), mirroring the
        # status GET filter.
        qs = KillSwitchAuditLog.objects.filter(organization=org).exclude(
            model_name__in=list(platform_guard_model_names())
        )
        if model_name:
            qs = qs.filter(model_name=model_name)
        logs = qs[:limit]
        data = KillSwitchAuditLogSerializer(logs, many=True).data
        # De-leak raw guard/upstream model ids that the exact-match exclude above
        # misses (e.g. an audit row written under a variant id like
        # 'anthropic/claude-haiku-4.5'). Canonicalize the displayed model_name via
        # the same token-based mapper the threat-feed uses — audit rows stay
        # visible but reserved ids collapse to the public 'zeroshield-model' label.
        for row in data:
            if isinstance(row, dict) and row.get("model_name"):
                row["model_name"] = canonicalize_model_name_safe(row["model_name"])
        return Response(data)
