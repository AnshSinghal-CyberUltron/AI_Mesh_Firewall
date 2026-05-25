"""ViewSet for VectorProviderConfig CRUD and Redis sync trigger."""

import logging

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from auth.utils import get_request_organization

from policy.vector_provider_models import VectorProviderConfig
from policy.vector_provider_serializers import (
    VectorProviderConfigReadSerializer,
    VectorProviderConfigWriteSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(
        tags=["Vector Providers"],
        summary="List organisation vector provider configurations",
        responses={200: VectorProviderConfigReadSerializer(many=True)},
    ),
    create=extend_schema(
        tags=["Vector Providers"],
        summary="Create or set a vector provider configuration",
        request=VectorProviderConfigWriteSerializer,
        responses={201: VectorProviderConfigReadSerializer},
    ),
    retrieve=extend_schema(
        tags=["Vector Providers"],
        summary="Retrieve a vector provider configuration",
        responses={200: VectorProviderConfigReadSerializer},
    ),
    partial_update=extend_schema(
        tags=["Vector Providers"],
        summary="Update a vector provider configuration",
        request=VectorProviderConfigWriteSerializer,
        responses={200: VectorProviderConfigReadSerializer},
    ),
    destroy=extend_schema(
        tags=["Vector Providers"],
        summary="Delete a vector provider configuration",
        responses={204: None},
    ),
)
class VectorProviderConfigViewSet(ModelViewSet):
    """CRUD for organisation-level vector DB provider credentials."""

    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def _scoped_org(self):
        return get_request_organization(self.request)

    def get_queryset(self):
        org = self._scoped_org()
        qs = VectorProviderConfig.objects.filter(organization=org) if org else VectorProviderConfig.objects.none()
        provider_type = self.request.query_params.get("provider_type")
        if provider_type:
            qs = qs.filter(provider_type=provider_type)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("true", "1", "yes"))
        return qs

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return VectorProviderConfigWriteSerializer
        return VectorProviderConfigReadSerializer

    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org = self._scoped_org()
        if org is None:
            return Response(
                {"detail": "Organization scope is required for vector provider configuration."},
                status=status.HTTP_403_FORBIDDEN,
            )
        instance = serializer.save(organization=org)
        logger.info("Created VectorProviderConfig %s for org=%s", instance.provider_type, org)
        return Response(
            VectorProviderConfigReadSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request: Request, *args, **kwargs) -> Response:
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info("Updated VectorProviderConfig %s (id=%s)", instance.provider_type, instance.pk)
        return Response(VectorProviderConfigReadSerializer(instance).data)
