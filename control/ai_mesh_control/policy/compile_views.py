"""
API views for policy compilation management.

POST /api/policies/compile/         -- Force recompilation (admin only)
GET  /api/policies/compile/status/  -- View current compiled state from Redis
"""

import json
import logging

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from auth.utils import get_request_organization
from core.admin_views import IsAdminOrSuperuser
from policy.compiler import (
    REDIS_KEY_COMPILED,
    REDIS_KEY_VERSION,
    PolicyCompiler,
    _get_redis_client,
)

logger = logging.getLogger(__name__)


class PolicyCompileThrottle(UserRateThrottle):
    """Throttle PolicyCompileView. Each compile fans out to every gateway via
    Redis pub/sub and recomputes the full org bundle; cap at 10/min/user to
    prevent authorized-admin compile floods while leaving headroom for legit
    rapid edits."""

    scope = "policy_compile"
    rate = "10/min"


class PolicyCompileView(APIView):
    """Force policy recompilation and push to Redis."""

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]
    throttle_classes = [PolicyCompileThrottle]

    @extend_schema(
        tags=["Policies"],
        summary="Force policy recompilation",
        description=(
            "Recompile all enabled policies and push the bundle to Redis. "
            "Triggers a Pub/Sub notification on `policy_updates` channel.\n\n"
            "**Permission:** Admin (superuser, staff, or platform_admin)."
        ),
        request=None,
        responses={
            200: inline_serializer(
                name="PolicyCompileResponse",
                fields={
                    "status": drf_serializers.CharField(),
                    "version": drf_serializers.IntegerField(),
                    "policy_count": drf_serializers.IntegerField(),
                    "compiled_at": drf_serializers.FloatField(),
                },
            ),
            500: inline_serializer(
                name="PolicyCompileErrorResponse",
                fields={"detail": drf_serializers.CharField()},
            ),
        },
    )
    def post(self, request: Request) -> Response:
        org = get_request_organization(request)
        if org is None:
            return Response(
                {"detail": "Organization scope is required to compile policies."},
                status=status.HTTP_403_FORBIDDEN,
            )
        # Defense-in-depth: the bundle compiled below is scoped to `org`, so
        # assert the requesting user actually belongs to that org before
        # compiling/pushing. R11: cross-org compile-push is reserved for
        # PLATFORM OPERATORS only (is_staff AND profile.is_platform_operator),
        # the same contract get_request_organization enforces for
        # ?organization_id. A plain superuser or per-tenant org admin is
        # ORG-scoped: even though they may carry is_superuser, they may only
        # compile/push their OWN org's bundle (product rule: "org admins and
        # superusers are ORG-scoped only, NO global cross-org"). This guard
        # makes the invariant explicit so a future org-resolution change can
        # never let an admin of org A compile and push org B's bundle.
        from auth.models import is_platform_operator

        if not is_platform_operator(request.user):
            profile_org_id = getattr(
                getattr(request.user, "profile", None), "organization_id", None
            )
            if profile_org_id != org.pk:
                logger.warning(
                    "Policy compile denied: user=%s (org=%s) requested bundle for org=%s",
                    request.user.pk,
                    profile_org_id,
                    org.pk,
                )
                return Response(
                    {"detail": "You may only compile policies for your own organization."},
                    status=status.HTTP_403_FORBIDDEN,
                )
        compiler = PolicyCompiler()
        bundle = compiler.compile_all(organization=org)
        success = compiler.push_to_redis(
            bundle,
            trigger="manual",
            changed_policy_ids=[],
            organization=org,
        )
        if not success:
            return Response(
                {"detail": "Failed to push compiled policies to Redis."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {
                "status": "compiled",
                "version": bundle.get("version"),
                "policy_count": bundle.get("policy_count", 0),
                "rule_count": bundle.get("rule_count", 0),
                "compiled_at": bundle.get("compiled_at"),
            }
        )


class PolicyCompileStatusView(APIView):
    """View the current compiled policy state from Redis."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Policies"],
        summary="Get compiled policy status",
        description=(
            "Read the current compiled policy bundle metadata from Redis.\n\n"
            "Returns version, policy count, compilation timestamp, and per-policy summary.\n\n"
            "**Permission:** Authenticated users."
        ),
        responses={
            200: inline_serializer(
                name="PolicyCompileStatusResponse",
                fields={
                    "redis_available": drf_serializers.BooleanField(),
                    "version": drf_serializers.IntegerField(allow_null=True),
                    "policy_count": drf_serializers.IntegerField(allow_null=True),
                    "rule_count": drf_serializers.IntegerField(allow_null=True),
                    "compiled_at": drf_serializers.FloatField(allow_null=True),
                    "policies": drf_serializers.ListField(
                        child=drf_serializers.DictField(),
                    ),
                },
            ),
        },
    )
    def get(self, request: Request) -> Response:
        org = get_request_organization(request)
        if org is None and not request.user.is_superuser:
            return Response(
                {"detail": "Organization scope required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        suffix = "default" if org is None else org.slug
        redis_key = f"{REDIS_KEY_COMPILED}:{suffix}"
        version_key = f"{REDIS_KEY_VERSION}:{suffix}"

        try:
            client = _get_redis_client()
            version = client.get(version_key)
            raw_bundle = client.get(redis_key)
        except Exception:
            logger.exception("Failed to read compiled policy state from Redis")
            return Response(
                {
                    "redis_available": False,
                    "version": None,
                    "policy_count": None,
                    "compiled_at": None,
                    "policies": [],
                }
            )

        if raw_bundle is None:
            return Response(
                {
                    "redis_available": True,
                    "version": int(version) if version else None,
                    "policy_count": None,
                    "compiled_at": None,
                    "policies": [],
                }
            )

        bundle = json.loads(raw_bundle)
        policy_summaries = []
        for entry in bundle.get("policies", []):
            p = entry.get("policy", {})
            policy_summaries.append(
                {
                    "id": p.get("id"),
                    "code": p.get("code"),
                    "name": p.get("name"),
                    "severity": p.get("severity"),
                    "rule_count": len(entry.get("rules", [])),
                }
            )

        return Response(
            {
                "redis_available": True,
                "version": int(version) if version else bundle.get("version"),
                "policy_count": bundle.get("policy_count"),
                "rule_count": bundle.get("rule_count") or sum(s.get("rule_count", 0) for s in policy_summaries),
                "compiled_at": bundle.get("compiled_at"),
                "policies": policy_summaries,
            }
        )
