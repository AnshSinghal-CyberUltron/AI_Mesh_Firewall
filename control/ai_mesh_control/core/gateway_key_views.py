"""ViewSet for GatewayAPIKey CRUD operations."""

import logging

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from core.gateway_serializers import (
    GatewayAPIKeyCreateSerializer,
    GatewayAPIKeySerializer,
    GatewayAPIKeyUpdateSerializer,
)
from core.models import GatewayAPIKey
from core.pagination import PublicUrlPagination

logger = logging.getLogger(__name__)

_ORG_ADMIN_ROLE_NAMES = ("admin", "superadmin", "org_admin")


class GatewayAPIKeyPagination(PublicUrlPagination):
    """Keys admin UI needs more than the global PAGE_SIZE=10 default."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500


def _profile_is_org_admin(profile) -> bool:
    """True when profile has an org-admin role via the roles M2M (never profile.role)."""
    if profile is None:
        return False
    return profile.roles.filter(name__in=_ORG_ADMIN_ROLE_NAMES).exists()


# ── SEC-07 FIX: Owner-only permission for key mutation ──
class IsGatewayKeyOwner(BasePermission):
    """
    Ensures users can only modify/delete API keys they own.
    Users can see all keys in their org (list), but can only mutate their own.
    """

    def has_object_permission(self, request, view, obj):
        # Always allow safe methods (GET, HEAD, OPTIONS) for keys in user's org
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        # For mutation (PATCH, DELETE), require ownership or admin role
        user = request.user
        if obj.owner_id == user.id:
            return True
        # Allow org admins to manage any key in their org
        profile = getattr(user, "profile", None)
        if profile and profile.organization_id == obj.organization_id:
            # #43: UserProfile has a `roles` M2M (auth/models.py) — there is NO
            # singular `role` attribute, so the old `profile.role in (...)` raised
            # AttributeError → HTTP 500, BREAKING the org-admin-manages-any-key path
            # (a same-org non-owner mutation hit this branch and 500'd instead of the
            # intended allow/deny). Query the M2M by role name (codebase idiom, cf.
            # core/models.py `profile.roles.values_list("name", ...)`).
            if _profile_is_org_admin(profile):
                return True
        return False

_ID_PATH_PARAM = [
    OpenApiParameter(
        name="id",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.PATH,
        description=(
            "UUID of the Gateway API Key. "
            "Copy this from the `id` field in the list response (`GET /api/gateways/keys/`)."
        ),
        required=True,
    ),
]


@extend_schema_view(
    list=extend_schema(
        tags=["Gateway API Keys"],
        summary="List your API keys",
        description=(
            "Returns all Gateway API Keys owned by the authenticated user, ordered by creation date (newest first).\n\n"
            "The response never includes the plaintext key or the key hash. "
            "Use the `prefix` field (first 8 characters) to identify keys in logs.\n\n"
            "**Authentication:** JWT required."
        ),
        responses={200: GatewayAPIKeySerializer(many=True)},
        examples=[
            OpenApiExample(
                "List of keys",
                value={
                    "count": 2,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                            "prefix": "zs_xK9mQ",
                            "name": "prod-rag-service",
                            "project_id": "proj-001",
                            "permissions": {
                                "allowed_actions": ["chat", "completion", "embedding"],
                                "denied_actions": [],
                            },
                            "allowed_models": ["gpt-4o-mini"],
                            "rate_limit_tokens_per_minute": 50000,
                            "risk_score": 0.0,
                            "is_active": True,
                            "expires_at": None,
                            "last_used_at": "2025-01-15T10:30:00Z",
                            "created_at": "2025-01-10T08:00:00Z",
                            "updated_at": "2025-01-15T10:30:00Z",
                        },
                        {
                            "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                            "prefix": "zs_pL3nR",
                            "name": "staging-chatbot",
                            "project_id": "proj-002",
                            "permissions": {"allowed_actions": ["chat"], "denied_actions": ["embedding"]},
                            "allowed_models": [],
                            "rate_limit_tokens_per_minute": 100000,
                            "risk_score": 0.1,
                            "is_active": True,
                            "expires_at": "2025-12-31T23:59:59Z",
                            "last_used_at": None,
                            "created_at": "2025-01-12T14:00:00Z",
                            "updated_at": "2025-01-12T14:00:00Z",
                        },
                    ],
                },
                response_only=True,
            ),
        ],
    ),
    create=extend_schema(
        tags=["Gateway API Keys"],
        summary="Generate a new API key",
        description=(
            "Creates a new Gateway API Key and returns the **plaintext key exactly once**.\n\n"
            "**Store the `key` value immediately.** It is never stored on the server and cannot be retrieved later. "
            "If lost, revoke the key and generate a new one.\n\n"
            "The key is automatically synced to Redis for zero-latency Gateway authentication. "
            "Use this key in the `Authorization: Bearer <key>` header when calling the Gateway "
            "(`POST /v1/chat/completions`).\n\n"
            "**Authentication:** JWT required.\n\n"
            "---\n"
            "### Workflow\n"
            "1. Call this endpoint with a name and project_id\n"
            "2. Copy the `key` from the response and store it securely\n"
            "3. Use the key as `Authorization: Bearer <key>` in Gateway requests\n"
            "4. The key is validated by the Gateway via Redis lookup (sub-millisecond)\n"
        ),
        request=GatewayAPIKeyCreateSerializer,
        responses={201: GatewayAPIKeySerializer},
        examples=[
            OpenApiExample(
                "Create key for production RAG service",
                value={
                    "name": "prod-rag-service",
                    "project_id": "proj-001",
                    "allowed_models": ["gpt-4o-mini"],
                    "rate_limit_tokens_per_minute": 50000,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Create key with full options",
                value={
                    "name": "staging-chatbot",
                    "project_id": "proj-002",
                    "permissions": {"allowed_actions": ["chat"], "denied_actions": ["embedding", "fine-tuning"]},
                    "allowed_models": ["gpt-4o-mini", "claude-3-haiku"],
                    "rate_limit_tokens_per_minute": 200000,
                    "risk_score": 0.1,
                    "expires_at": "2025-12-31T23:59:59Z",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Minimal key (all defaults)",
                value={
                    "name": "dev-testing",
                    "project_id": "proj-dev",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Key created successfully",
                value={
                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "prefix": "zs_xK9mQ",
                    "name": "prod-rag-service",
                    "project_id": "proj-001",
                    "permissions": {"allowed_actions": ["chat", "completion", "embedding"], "denied_actions": []},
                    "allowed_models": ["gpt-4o-mini"],
                    "rate_limit_tokens_per_minute": 50000,
                    "risk_score": 0.0,
                    "is_active": True,
                    "expires_at": None,
                    "last_used_at": None,
                    "created_at": "2025-01-10T08:00:00Z",
                    "updated_at": "2025-01-10T08:00:00Z",
                    "key": "zs_xK9mQp7Lw2nR4tY6uI8oP0aS3dF5gH7jK9lZ1xC2vB4nM6",
                    "warning": "Store this key securely. It will not be shown again.",
                },
                response_only=True,
                status_codes=["201"],
            ),
        ],
    ),
    retrieve=extend_schema(
        tags=["Gateway API Keys"],
        summary="Get API key details",
        description=(
            "Retrieve details of a specific Gateway API Key by its UUID.\n\n"
            "The response includes configuration, usage stats, and status but never the plaintext key or hash.\n\n"
            "**Authentication:** JWT required. Only keys owned by the authenticated user are accessible."
        ),
        parameters=_ID_PATH_PARAM,
        responses={200: GatewayAPIKeySerializer},
        examples=[
            OpenApiExample(
                "Key details",
                value={
                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "prefix": "zs_xK9mQ",
                    "name": "prod-rag-service",
                    "project_id": "proj-001",
                    "permissions": {"allowed_actions": ["chat", "completion", "embedding"], "denied_actions": []},
                    "allowed_models": ["gpt-4o-mini"],
                    "rate_limit_tokens_per_minute": 50000,
                    "risk_score": 0.0,
                    "is_active": True,
                    "expires_at": None,
                    "last_used_at": "2025-01-15T10:30:00Z",
                    "created_at": "2025-01-10T08:00:00Z",
                    "updated_at": "2025-01-15T10:30:00Z",
                },
                response_only=True,
            ),
        ],
    ),
    partial_update=extend_schema(
        tags=["Gateway API Keys"],
        summary="Update API key settings",
        description=(
            "Partially update a Gateway API Key. Only include the fields you want to change.\n\n"
            "Common operations:\n"
            '- **Disable a key:** `{"is_active": false}`\n'
            '- **Re-enable a key:** `{"is_active": true}`\n'
            '- **Update rate limit:** `{"rate_limit_tokens_per_minute": 200000}`\n'
            '- **Restrict models:** `{"allowed_models": ["gpt-4o-mini"]}`\n'
            '- **Remove model restrictions:** `{"allowed_models": []}`\n'
            '- **Update permissions:** `{"permissions": {"allowed_actions": ["chat"], "denied_actions": ["embedding"]}}`\n\n'
            "Changes are automatically propagated to the Gateway via Redis sync.\n\n"
            "**Authentication:** JWT required."
        ),
        parameters=_ID_PATH_PARAM,
        request=GatewayAPIKeyUpdateSerializer,
        responses={200: GatewayAPIKeySerializer},
        examples=[
            OpenApiExample(
                "Disable key",
                value={"is_active": False},
                request_only=True,
            ),
            OpenApiExample(
                "Re-enable key",
                value={"is_active": True},
                request_only=True,
            ),
            OpenApiExample(
                "Update rate limit and model allowlist",
                value={
                    "rate_limit_tokens_per_minute": 200000,
                    "allowed_models": ["gpt-4o-mini", "gpt-4o", "claude-3-sonnet"],
                },
                request_only=True,
            ),
            OpenApiExample(
                "Rename key",
                value={"name": "prod-rag-service-v2"},
                request_only=True,
            ),
            OpenApiExample(
                "Set expiry",
                value={"expires_at": "2026-06-30T23:59:59Z"},
                request_only=True,
            ),
        ],
    ),
    destroy=extend_schema(
        tags=["Gateway API Keys"],
        summary="Revoke API key",
        description=(
            "Permanently revoke and delete a Gateway API Key.\n\n"
            "The key is immediately removed from both the database and the Redis cache. "
            "Any Gateway requests using this key will be rejected with `401 Unauthorized` "
            "within milliseconds of revocation.\n\n"
            "**This action is irreversible.** To temporarily disable a key without deleting it, "
            'use PATCH with `{"is_active": false}` instead.\n\n'
            "**Authentication:** JWT required."
        ),
        parameters=_ID_PATH_PARAM,
        responses={204: None},
    ),
)
class GatewayAPIKeyViewSet(ModelViewSet):
    """CRUD for Gateway API Keys. Keys authenticate requests to the Gateway Data Plane proxy."""

    # SEC-07 FIX: Add object-level permission for key mutation
    permission_classes = [IsAuthenticated, IsGatewayKeyOwner]
    pagination_class = GatewayAPIKeyPagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        org = getattr(getattr(user, "profile", None), "organization", None)
        if org:
            return GatewayAPIKey.objects.filter(organization=org).order_by("-created_at")
        return GatewayAPIKey.objects.filter(owner=user).order_by("-created_at")

    def get_serializer_class(self):
        if self.action == "create":
            return GatewayAPIKeyCreateSerializer
        if self.action in ("update", "partial_update"):
            return GatewayAPIKeyUpdateSerializer
        return GatewayAPIKeySerializer

    def create(self, request, *args, **kwargs):
        # Pass request context so the serializer can gate reserved privileged
        # permission flags (admin/playground) on platform-operator status.
        serializer = GatewayAPIKeyCreateSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        instance, plaintext_key = GatewayAPIKey.generate_key(
            name=validated["name"],
            owner=request.user,
            project_id=validated["project_id"],
            permissions=validated.get("permissions"),
            allowed_models=validated.get("allowed_models"),
            rate_limit_tokens_per_minute=validated.get("rate_limit_tokens_per_minute"),
            risk_score=validated.get("risk_score"),
            expires_at=validated.get("expires_at"),
            max_context_tokens=validated.get("max_context_tokens", 0),
        )

        logger.info(
            "GatewayAPIKey created: prefix=%s user_id=%s project=%s",
            instance.prefix,
            request.user.pk,
            instance.project_id,
        )

        response_data = GatewayAPIKeySerializer(instance).data
        response_data["key"] = plaintext_key
        response_data["warning"] = "Store this key securely. It will not be shown again."

        return Response(response_data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        logger.info(
            "GatewayAPIKey revoked: prefix=%s user_id=%s project=%s",
            instance.prefix,
            self.request.user.pk,
            instance.project_id,
        )
        instance.delete()

    def _revoked_keys_for_bulk_purge(self):
        """Revoked keys the caller may permanently delete (owner or org-admin).

        Active keys are never included. Org admins purge all revoked keys in the
        org-scoped list queryset; everyone else only their own revoked keys.
        """
        qs = self.get_queryset().filter(is_active=False)
        user = self.request.user
        profile = getattr(user, "profile", None)
        if _profile_is_org_admin(profile):
            return qs
        return qs.filter(owner=user)

    @extend_schema(
        tags=["Gateway API Keys"],
        summary="Permanently delete all revoked API keys",
        description=(
            "Hard-deletes every **revoked** (`is_active=false`) Gateway API Key the "
            "caller is allowed to manage.\n\n"
            "- **Org admins** (`admin` / `superadmin` / `org_admin`): all revoked keys "
            "in their organization.\n"
            "- **Other users**: only their own revoked keys.\n\n"
            "Each delete removes the row from Postgres and clears `auth:apikey:{hash}` "
            "from Redis via the existing post_delete signal. Active keys are never touched.\n\n"
            "**This action is irreversible.**\n\n"
            "**Authentication:** JWT required."
        ),
        responses={
            200: OpenApiTypes.OBJECT,
        },
        examples=[
            OpenApiExample(
                "Purge result",
                value={"deleted": 7, "ids": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890"]},
                response_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["delete", "post"], url_path="purge-revoked")
    def purge_revoked(self, request, *args, **kwargs):
        qs = self._revoked_keys_for_bulk_purge()
        # Materialize before delete so we can return ids. Include key_hash:
        # post_delete Redis sync reads instance.key_hash; a deferred field after
        # DELETE would refresh_from_db → DoesNotExist → HTTP 500.
        targets = list(qs.only("id", "prefix", "project_id", "owner_id", "key_hash"))
        deleted_ids = []
        for instance in targets:
            deleted_ids.append(str(instance.id))
            logger.info(
                "GatewayAPIKey bulk-purge revoked: prefix=%s user_id=%s project=%s",
                instance.prefix,
                request.user.pk,
                instance.project_id,
            )
            instance.delete()
        return Response(
            {"deleted": len(deleted_ids), "ids": deleted_ids},
            status=status.HTTP_200_OK,
        )


class GatewayKeyContextView(APIView):
    """POST /api/gateways/keys/context/ — resolve plaintext key to UEBA fleet identity."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw = str(request.data.get("api_key") or "").strip()
        if not raw:
            return Response({"detail": "api_key is required."}, status=status.HTTP_400_BAD_REQUEST)

        from auth.utils import get_request_organization

        org = get_request_organization(request.user)
        key_hash = GatewayAPIKey.hash_raw_key(raw)
        qs = GatewayAPIKey.objects.filter(key_hash=key_hash, is_active=True)
        if org:
            qs = qs.filter(organization=org)
        elif not getattr(request.user, "is_superuser", False):
            qs = qs.filter(owner=request.user)

        key = qs.first()
        if key is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        project_id = str(key.project_id or "")
        is_simulator = project_id.startswith("simulator-") or key.name == "simulator"
        storage_key = (
            f"zeroshield_gateway_key:{key.organization_id}"
            if key.organization_id
            else "zeroshield_gateway_key"
        )
        return Response(
            {
                "prefix": key.prefix,
                "key_id": str(key.id),
                "name": key.name,
                "project_id": key.project_id,
                "is_simulator_default": is_simulator,
                "storage_key": storage_key,
            }
        )
