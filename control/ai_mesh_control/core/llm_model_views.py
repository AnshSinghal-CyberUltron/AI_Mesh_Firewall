"""API views for LLMModelConfig CRUD operations."""

import json
import logging

from django.db import IntegrityError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.llm_model_serializer import LLMModelConfigSerializer
from core.models import AuditLog, LLMModelConfig

logger = logging.getLogger(__name__)

AUDIT_ACTION_CREATED = "llm_model_credential.created"
AUDIT_ACTION_UPDATED = "llm_model_credential.updated"
AUDIT_ACTION_DELETED = "llm_model_credential.deleted"


def _client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _audit_model_credential(
    request,
    action: str,
    *,
    model_name: str = "",
    provider: str = "",
    model_pk: int | None = None,
    api_key_changed: bool = False,
    api_key_cleared: bool = False,
) -> None:
    org = getattr(getattr(request.user, "profile", None), "organization", None)
    resource = f"llm_model:{model_pk}" if model_pk is not None else f"llm_model:{model_name}"
    details = {
        "model_name": model_name,
        "provider": provider,
        "api_key_changed": api_key_changed,
        "api_key_cleared": api_key_cleared,
    }
    if org is not None:
        details["organization_id"] = org.id
        details["organization_slug"] = org.slug
    try:
        AuditLog.objects.create(
            user=request.user if getattr(request.user, "is_authenticated", False) else None,
            organization=org,
            action=action,
            resource=resource,
            details=json.dumps(details, sort_keys=True),
            ip_address=_client_ip(request),
        )
    except Exception:
        logger.exception(
            "Failed to write model credential audit log: action=%s model=%s",
            action,
            model_name,
        )


def _api_key_change_flags(request_data) -> tuple[bool, bool]:
    if "api_key" not in request_data:
        return False, False
    raw = request_data.get("api_key")
    if raw is None:
        return False, False
    stripped = str(raw).strip()
    if not stripped:
        return False, True
    return True, False


class LLMModelConfigListView(APIView):
    """
    GET  /api/firewall/models/  -- list all LLM model configurations
    POST /api/firewall/models/  -- create a new model configuration
    """

    permission_classes = [IsAuthenticated]

    def _get_org(self, request):
        return getattr(getattr(request.user, "profile", None), "organization", None)

    def get(self, request):
        org = self._get_org(request)
        if org is not None:
            qs = LLMModelConfig.objects.filter(organization=org)
        elif getattr(request.user, "is_superuser", False):
            qs = LLMModelConfig.objects.all()
        else:
            qs = LLMModelConfig.objects.none()
        qs = LLMModelConfig.queryset_user_managed(qs)
        serializer = LLMModelConfigSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        # R14: a non-dict JSON body (bare list/string) reaches the serializer and
        # _api_key_change_flags(), both of which assume a mapping → unhandled 500.
        if not isinstance(request.data, dict):
            return Response(
                {"detail": "Request body must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = LLMModelConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org = self._get_org(request)
        try:
            instance = serializer.save(organization=org)
        except IntegrityError:
            # (organization, model_name) is unique — a duplicate add must be a
            # clean 400, not a 500 (surfaced via the Multi-Model Governance UI).
            return Response(
                {"detail": "A model with this name is already connected for your organization."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        key_changed, key_cleared = _api_key_change_flags(request.data)
        _audit_model_credential(
            request,
            AUDIT_ACTION_CREATED,
            model_name=instance.model_name,
            provider=instance.provider,
            model_pk=instance.pk,
            api_key_changed=key_changed,
            api_key_cleared=key_cleared,
        )
        logger.info(
            "LLMModelConfig created: model_name=%s, provider=%s by user=%s",
            instance.model_name,
            instance.provider,
            request.user.username,
        )
        return Response(
            LLMModelConfigSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )


class LLMModelConfigDetailView(APIView):
    """
    GET    /api/firewall/models/<pk>/  -- retrieve a single model config
    PUT    /api/firewall/models/<pk>/  -- update a model config
    DELETE /api/firewall/models/<pk>/  -- delete a model config
    """

    permission_classes = [IsAuthenticated]

    def _get_org(self, request):
        return getattr(getattr(request.user, "profile", None), "organization", None)

    def _get_object(self, request, pk):
        org = self._get_org(request)
        try:
            obj = LLMModelConfig.objects.get(pk=pk)
        except LLMModelConfig.DoesNotExist:
            return None
        if org is not None and obj.organization_id != org.id:
            return None
        if org is None and not getattr(request.user, "is_superuser", False):
            return None
        if obj.is_platform_managed:
            return None
        return obj

    def get(self, request, pk):
        obj = self._get_object(request, pk)
        if obj is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = LLMModelConfigSerializer(obj)
        return Response(serializer.data)

    def put(self, request, pk):
        obj = self._get_object(request, pk)
        if obj is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        # R14: reject a non-dict JSON body before it reaches the serializer and
        # _api_key_change_flags() / list(request.data.keys()), which assume a dict.
        if not isinstance(request.data, dict):
            return Response(
                {"detail": "Request body must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = LLMModelConfigSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        key_changed, key_cleared = _api_key_change_flags(request.data)
        _audit_model_credential(
            request,
            AUDIT_ACTION_UPDATED,
            model_name=instance.model_name,
            provider=instance.provider,
            model_pk=instance.pk,
            api_key_changed=key_changed,
            api_key_cleared=key_cleared,
        )
        logger.info(
            "LLMModelConfig updated: model_name=%s (fields=%s) by user=%s",
            instance.model_name,
            list(request.data.keys()),
            request.user.username,
        )
        return Response(LLMModelConfigSerializer(instance).data)

    def patch(self, request, pk):
        return self.put(request, pk)

    def delete(self, request, pk):
        obj = self._get_object(request, pk)
        if obj is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        model_name = obj.model_name
        provider = obj.provider
        model_pk = obj.pk
        had_key = obj.api_key_set
        obj.delete()
        _audit_model_credential(
            request,
            AUDIT_ACTION_DELETED,
            model_name=model_name,
            provider=provider,
            model_pk=model_pk,
            api_key_changed=False,
            api_key_cleared=had_key,
        )
        logger.info(
            "LLMModelConfig deleted: model_name=%s by user=%s",
            model_name,
            request.user.username,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
