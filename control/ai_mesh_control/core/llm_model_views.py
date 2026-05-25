"""API views for LLMModelConfig CRUD operations."""

import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.llm_model_serializer import LLMModelConfigSerializer
from core.models import LLMModelConfig

logger = logging.getLogger(__name__)


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
        serializer = LLMModelConfigSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = LLMModelConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org = self._get_org(request)
        instance = serializer.save(organization=org)
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
        serializer = LLMModelConfigSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
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
        obj.delete()
        logger.info(
            "LLMModelConfig deleted: model_name=%s by user=%s",
            model_name,
            request.user.username,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
