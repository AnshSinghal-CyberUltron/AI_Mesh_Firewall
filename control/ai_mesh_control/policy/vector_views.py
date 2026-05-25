"""ViewSet for VectorCollectionPolicy CRUD operations and compilation trigger."""

import logging

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from auth.utils import get_request_organization

from policy.vector_models import VectorCollectionPolicy
from policy.vector_serializers import (
    VectorCollectionPolicyCreateSerializer,
    VectorCollectionPolicySerializer,
    VectorCollectionPolicyUpdateSerializer,
)

logger = logging.getLogger(__name__)

_ID_PATH_PARAM = [
    OpenApiParameter(
        name="id",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.PATH,
        description="UUID of the Vector Collection Policy.",
        required=True,
    ),
]


@extend_schema_view(
    list=extend_schema(
        tags=["Vector Policies"],
        summary="List vector collection policies",
        description=(
            "Returns all vector collection policies, optionally filtered by\n"
            "project_id, collection_name, vector_db_type, or enabled status.\n\n"
            "**Authentication:** JWT required."
        ),
        responses={200: VectorCollectionPolicySerializer(many=True)},
    ),
    create=extend_schema(
        tags=["Vector Policies"],
        summary="Create a vector collection policy",
        description=(
            "Creates a new access control policy for a vector DB collection.\n\n"
            "The policy defines namespace isolation, allowed operations,\n"
            "content filtering rules, and embedding anomaly thresholds.\n\n"
            "On creation, the policy is automatically compiled to Redis\n"
            "for zero-latency Gateway enforcement.\n\n"
            "**Authentication:** JWT required."
        ),
        request=VectorCollectionPolicyCreateSerializer,
        responses={201: VectorCollectionPolicySerializer},
        examples=[
            OpenApiExample(
                "Create policy for customer docs",
                value={
                    "name": "Customer Docs Access",
                    "project_id": "proj-001",
                    "collection_name": "customer_docs",
                    "vector_db_type": "pinecone",
                    "default_action": "allow",
                    "allowed_operations": ["query"],
                    "max_results_per_query": 10,
                    "max_query_length": 2000,
                    "require_context_scan": True,
                    "block_sensitive_documents": True,
                    "anomaly_distance_threshold": 0.85,
                },
                request_only=True,
            ),
        ],
    ),
    retrieve=extend_schema(
        tags=["Vector Policies"],
        summary="Retrieve a vector collection policy",
        description="Get details of a specific vector collection policy by UUID.",
        parameters=_ID_PATH_PARAM,
        responses={200: VectorCollectionPolicySerializer},
    ),
    update=extend_schema(
        tags=["Vector Policies"],
        summary="Update a vector collection policy",
        description="Full update of a vector collection policy.",
        parameters=_ID_PATH_PARAM,
        request=VectorCollectionPolicyUpdateSerializer,
        responses={200: VectorCollectionPolicySerializer},
    ),
    partial_update=extend_schema(
        tags=["Vector Policies"],
        summary="Partial update a vector collection policy",
        description="Partial update -- only changed fields need to be sent.",
        parameters=_ID_PATH_PARAM,
        request=VectorCollectionPolicyUpdateSerializer,
        responses={200: VectorCollectionPolicySerializer},
    ),
    destroy=extend_schema(
        tags=["Vector Policies"],
        summary="Delete a vector collection policy",
        description=(
            "Permanently deletes a vector collection policy.\n\n"
            "This triggers recompilation of the Redis bundle so the\n"
            "Gateway immediately stops enforcing this policy."
        ),
        parameters=_ID_PATH_PARAM,
        responses={204: None},
    ),
)
class VectorCollectionPolicyViewSet(ModelViewSet):
    """CRUD ViewSet for VectorCollectionPolicy with full OpenAPI documentation."""

    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def _scoped_org(self):
        return get_request_organization(self.request)

    def get_queryset(self):
        org = self._scoped_org()
        qs = VectorCollectionPolicy.objects.filter(organization=org) if org else VectorCollectionPolicy.objects.none()
        project_id = self.request.query_params.get("project_id")
        if project_id:
            qs = qs.filter(project_id=project_id)
        collection_name = self.request.query_params.get("collection_name")
        if collection_name:
            qs = qs.filter(collection_name=collection_name)
        vector_db_type = self.request.query_params.get("vector_db_type")
        if vector_db_type:
            qs = qs.filter(vector_db_type=vector_db_type)
        enabled = self.request.query_params.get("enabled")
        if enabled is not None:
            qs = qs.filter(enabled=enabled.lower() in ("true", "1", "yes"))
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return VectorCollectionPolicyCreateSerializer
        if self.action in ("update", "partial_update"):
            return VectorCollectionPolicyUpdateSerializer
        return VectorCollectionPolicySerializer

    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org = self._scoped_org()
        if org is None:
            return Response(
                {"detail": "Organization scope is required for vector policy operations."},
                status=status.HTTP_403_FORBIDDEN,
            )
        instance = serializer.save(organization=org)
        logger.info(
            "Created VectorCollectionPolicy %s/%s (id=%s)",
            instance.project_id,
            instance.collection_name,
            instance.pk,
        )
        read_serializer = VectorCollectionPolicySerializer(instance)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request: Request, *args, **kwargs) -> Response:
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info(
            "Updated VectorCollectionPolicy %s/%s (id=%s)",
            instance.project_id,
            instance.collection_name,
            instance.pk,
        )
        read_serializer = VectorCollectionPolicySerializer(instance)
        return Response(read_serializer.data)


class VectorPolicyCompileView(APIView):
    """Force vector policy recompilation and push to Redis."""

    permission_classes = [IsAdminUser]

    @extend_schema(
        tags=["Vector Policies"],
        summary="Force vector policy recompilation",
        description=(
            "Recompile all enabled vector collection policies and push\n"
            "the bundle to Redis. Triggers a Pub/Sub notification on the\n"
            "``vector_policy_updates`` channel.\n\n"
            "**Permission:** Admin only."
        ),
        request=None,
        responses={
            200: inline_serializer(
                name="VectorPolicyCompileResponse",
                fields={
                    "status": drf_serializers.CharField(),
                    "version": drf_serializers.IntegerField(),
                    "policy_count": drf_serializers.IntegerField(),
                    "compiled_at": drf_serializers.FloatField(),
                },
            ),
            500: inline_serializer(
                name="VectorPolicyCompileErrorResponse",
                fields={"detail": drf_serializers.CharField()},
            ),
        },
    )
    def post(self, request: Request) -> Response:
        from policy.vector_compiler import VectorPolicyCompiler

        compiler = VectorPolicyCompiler()
        bundle = compiler.compile_all()
        success = compiler.push_to_redis(
            bundle,
            trigger="manual",
            changed_policy_ids=[],
        )
        if not success:
            return Response(
                {"detail": "Failed to push compiled vector policies to Redis."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {
                "status": "compiled",
                "version": bundle.get("version"),
                "policy_count": bundle.get("policy_count", 0),
                "compiled_at": bundle.get("compiled_at"),
            }
        )
