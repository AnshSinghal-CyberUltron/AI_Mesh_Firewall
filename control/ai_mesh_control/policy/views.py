from collections import defaultdict
from datetime import timedelta
import logging
import uuid

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from auth.utils import get_request_organization
from core.admin_views import IsAdminOrSuperuser

from .constants import ACTION_BLOCK, ACTION_REDACT
from .engine import VALID_POLICY_DOMAINS, validate_policy_domain
from .models import EnforcementEvent, Policy, PolicyVersion, Rule
from .serializers import (
    PolicyListWithStatsSerializer,
    PolicySerializer,
    PolicyVersionSerializer,
    PolicyWriteSerializer,
    RuleSerializer,
    RuleWriteSerializer,
)

_POLICY_ID_PATH_PARAM = [
    OpenApiParameter(
        name="id",
        type=OpenApiTypes.INT,
        location=OpenApiParameter.PATH,
        description=(
            "Integer ID of the policy. Copy this from the `id` field in the list response (`GET /api/policies/`)."
        ),
        required=True,
    ),
]

_RULE_ID_PATH_PARAM = [
    OpenApiParameter(
        name="id",
        type=OpenApiTypes.INT,
        location=OpenApiParameter.PATH,
        description=(
            "Integer ID of the rule. Copy this from the `id` field in the list response (`GET /api/policies/rules/`)."
        ),
        required=True,
    ),
]

logger = logging.getLogger(__name__)


def _user_is_policy_admin(request) -> bool:
    """Platform admin / staff / superuser — required to disable system policies or mutate their rules."""
    return IsAdminOrSuperuser().has_permission(request, None)


def _get_request_id(request):
    return request.headers.get("X-Request-ID") or request.META.get("HTTP_X_REQUEST_ID") or uuid.uuid4().hex[:12]


def _get_required_policy_domain(request):
    domain = request.query_params.get("policy_domain")
    return validate_policy_domain(domain)


class ConflictError(Exception):
    def __init__(self, policy, client_version):
        self.policy = policy
        self.client_version = client_version


@extend_schema_view(
    list=extend_schema(
        tags=["Policies"],
        summary="List policies",
        description=(
            "List all policies with optional filters. Returns paginated results.\n\n"
            "**Authentication:** JWT required.\n\n"
            "**Filters:**\n"
            "- `?enabled=true` -- Only enabled policies\n"
            "- `?enabled=false` -- Only disabled policies\n"
            "- `?category=Data Protection` -- Filter by category\n"
            "- `?severity=CRITICAL` -- Filter by severity (CRITICAL, HIGH, MEDIUM, LOW)\n\n"
            "**Ordering:** Results are sorted by priority (descending), then by code."
        ),
        parameters=[
            OpenApiParameter(name="enabled", type=bool, required=False, description="Filter by enabled status"),
            OpenApiParameter(name="category", type=str, required=False, description="Filter by category"),
            OpenApiParameter(
                name="severity",
                type=str,
                required=False,
                description="Filter by severity",
                enum=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
            ),
            OpenApiParameter(
                name="policy_domain",
                type=str,
                required=False,
                description="Filter by policy domain",
                enum=["pipeline", "rag", "mcp"],
            ),
            OpenApiParameter(
                name="days",
                type=int,
                required=False,
                description="Time window (days) for per-policy stats; default 30, max 90",
            ),
            OpenApiParameter(name="page", type=int, required=False, description="Page number"),
        ],
        examples=[
            OpenApiExample(
                "Paginated policy list",
                value={
                    "count": 3,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": 1,
                            "name": "Prompt Injection Blocker",
                            "code": "INJECTION_BLOCK",
                            "category": "Injection Prevention",
                            "severity": "CRITICAL",
                            "description": "Blocks prompt injection attempts",
                            "enabled": True,
                            "priority": 20,
                            "rule_count": 3,
                            "metadata": {},
                            "version": 1,
                            "created_at": "2025-01-10T08:00:00Z",
                            "updated_at": "2025-01-10T08:00:00Z",
                        },
                        {
                            "id": 2,
                            "name": "PII Detection",
                            "code": "PII_DETECT",
                            "category": "Data Protection",
                            "severity": "HIGH",
                            "description": "Detects and redacts PII in prompts/responses",
                            "enabled": True,
                            "priority": 15,
                            "rule_count": 4,
                            "metadata": {},
                            "version": 2,
                            "created_at": "2025-01-10T09:00:00Z",
                            "updated_at": "2025-01-12T14:00:00Z",
                        },
                    ],
                },
                response_only=True,
            ),
        ],
    ),
    create=extend_schema(
        tags=["Policies"],
        summary="Create policy",
        description=(
            "Create a new security policy. Rules can be added separately via the rules endpoint "
            "(`POST /api/policies/{id}/rules/`).\n\n"
            "**Authentication:** JWT required.\n\n"
            "**Workflow:**\n"
            "1. Create the policy (this endpoint)\n"
            "2. Add rules to it (`POST /api/policies/{id}/rules/`)\n"
            "3. Compile policies (`POST /api/policies/compile/`) to push to Gateway\n"
            "4. Test with a dry run (`POST /api/policies/test/`)\n\n"
            "**Fields:**\n"
            "- `code` -- Unique slug identifier (e.g. `PII_DETECT`). Used in enforcement events.\n"
            "- `severity` -- CRITICAL, HIGH, MEDIUM, or LOW.\n"
            "- `priority` -- Higher priority policies are evaluated first.\n"
            "- `enabled` -- Set to `false` to disable without deleting."
        ),
        examples=[
            OpenApiExample(
                "Create PII detection policy",
                value={
                    "name": "PII Detection",
                    "code": "PII_DETECT",
                    "category": "Data Protection",
                    "severity": "HIGH",
                    "description": "Detects and redacts PII (SSN, credit cards, emails) in prompts and responses",
                    "enabled": True,
                    "priority": 15,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Create prompt injection blocker",
                value={
                    "name": "Prompt Injection Blocker",
                    "code": "INJECTION_BLOCK",
                    "category": "Injection Prevention",
                    "severity": "CRITICAL",
                    "description": "Blocks prompt injection attempts using regex and keyword rules",
                    "enabled": True,
                    "priority": 20,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Create jailbreak monitor (monitor only, no block)",
                value={
                    "name": "Jailbreak Monitor",
                    "code": "JAILBREAK_MON",
                    "category": "Jailbreak Detection",
                    "severity": "MEDIUM",
                    "description": "Logs jailbreak attempts for analysis without blocking",
                    "enabled": True,
                    "priority": 5,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Policy created",
                value={
                    "id": 1,
                    "name": "PII Detection",
                    "code": "PII_DETECT",
                    "category": "Data Protection",
                    "severity": "HIGH",
                    "description": "Detects and redacts PII (SSN, credit cards, emails) in prompts and responses",
                    "enabled": True,
                    "priority": 15,
                    "metadata": {},
                    "version": 1,
                    "created_at": "2025-01-10T08:00:00Z",
                    "updated_at": "2025-01-10T08:00:00Z",
                },
                response_only=True,
                status_codes=["201"],
            ),
        ],
    ),
    retrieve=extend_schema(
        tags=["Policies"],
        summary="Get policy detail",
        description=("Get a single policy with all its rules.\n\n**Authentication:** JWT required."),
        parameters=_POLICY_ID_PATH_PARAM,
        examples=[
            OpenApiExample(
                "Policy with rules",
                value={
                    "id": 1,
                    "name": "PII Detection",
                    "code": "PII_DETECT",
                    "category": "Data Protection",
                    "severity": "HIGH",
                    "description": "Detects and redacts PII in prompts/responses",
                    "enabled": True,
                    "priority": 15,
                    "metadata": {},
                    "version": 2,
                    "rules": [
                        {
                            "id": 1,
                            "policy": 1,
                            "name": "Redact SSN",
                            "rule_type": "regex",
                            "condition": {"regex": "\\b\\d{3}-\\d{2}-\\d{4}\\b", "field": "both"},
                            "action": "redact",
                            "redaction_config": {"replacement": "[SSN_REDACTED]"},
                            "priority": 10,
                            "enabled": True,
                            "description": "Redacts Social Security Numbers",
                            "created_at": "2025-01-10T08:30:00Z",
                            "updated_at": "2025-01-10T08:30:00Z",
                        },
                        {
                            "id": 2,
                            "policy": 1,
                            "name": "Redact Credit Card",
                            "rule_type": "regex",
                            "condition": {"regex": "\\b\\d{4}[- ]?\\d{4}[- ]?\\d{4}[- ]?\\d{4}\\b", "field": "both"},
                            "action": "redact",
                            "redaction_config": {"replacement": "[CC_REDACTED]"},
                            "priority": 10,
                            "enabled": True,
                            "description": "Redacts credit card numbers",
                            "created_at": "2025-01-10T08:35:00Z",
                            "updated_at": "2025-01-10T08:35:00Z",
                        },
                    ],
                    "created_at": "2025-01-10T08:00:00Z",
                    "updated_at": "2025-01-12T14:00:00Z",
                },
                response_only=True,
            ),
        ],
    ),
    update=extend_schema(
        tags=["Policies"],
        summary="Update policy (full)",
        description=(
            "Full update of a policy. Include `version` in the request body for conflict detection.\n\n"
            "If the policy was modified by another user since you last fetched it, "
            "returns **409 Conflict** with the current version and latest snapshot.\n\n"
            "**Authentication:** JWT required.\n\n"
            "**Conflict detection:** Pass the `version` value you received when fetching the policy. "
            "If another user updated it in the meantime, the server rejects your update to prevent "
            "silent overwrites."
        ),
        parameters=_POLICY_ID_PATH_PARAM,
        responses={
            200: PolicySerializer,
            409: inline_serializer(
                name="PolicyConflictResponse",
                fields={
                    "detail": drf_serializers.CharField(),
                    "current_version": drf_serializers.IntegerField(),
                    "your_version": drf_serializers.IntegerField(),
                    "latest_snapshot": drf_serializers.DictField(),
                },
            ),
        },
        examples=[
            OpenApiExample(
                "Full update with conflict detection",
                value={
                    "name": "PII Detection v2",
                    "code": "PII_DETECT",
                    "category": "Data Protection",
                    "severity": "CRITICAL",
                    "description": "Updated PII detection with stricter rules",
                    "enabled": True,
                    "priority": 20,
                    "version": 2,
                },
                request_only=True,
            ),
        ],
    ),
    partial_update=extend_schema(
        tags=["Policies"],
        summary="Update policy (partial)",
        description=(
            "Partial update. Same conflict detection as full update.\n\n"
            "**Authentication:** JWT required.\n\n"
            "**Common operations:**\n"
            '- Enable: `{"enabled": true}`\n'
            '- Disable: `{"enabled": false}`\n'
            '- Change priority: `{"priority": 25}`\n'
            '- Change severity: `{"severity": "CRITICAL"}`'
        ),
        parameters=_POLICY_ID_PATH_PARAM,
        examples=[
            OpenApiExample(
                "Enable policy",
                value={"enabled": True},
                request_only=True,
            ),
            OpenApiExample(
                "Disable policy",
                value={"enabled": False},
                request_only=True,
            ),
            OpenApiExample(
                "Change priority",
                value={"priority": 25},
                request_only=True,
            ),
            OpenApiExample(
                "Upgrade severity",
                value={"severity": "CRITICAL"},
                request_only=True,
            ),
        ],
    ),
    destroy=extend_schema(
        tags=["Policies"],
        summary="Delete policy",
        description=(
            "Delete a policy and all its associated rules.\n\n"
            "**Authentication:** JWT required.\n\n"
            "**Warning:** This permanently removes the policy, all its rules, and their enforcement history references. "
            'To temporarily disable a policy without deleting, use PATCH with `{"enabled": false}` instead.\n\n'
            "After deletion, run `POST /api/policies/compile/` to update the Gateway cache."
        ),
        parameters=_POLICY_ID_PATH_PARAM,
    ),
)
class PolicyViewSet(ModelViewSet):
    """CRUD for policies. List/retrieve/create/update/destroy."""

    permission_classes = [IsAuthenticated]
    queryset = Policy.objects.all().prefetch_related("rules").order_by("-priority", "code")

    def get_serializer_class(self):
        if self.action == "list":
            return PolicyListWithStatsSerializer
        if self.action in ("create", "update", "partial_update"):
            return PolicyWriteSerializer
        return PolicySerializer

    def _get_policy_stats_map(self, policy_ids, since):
        """Bulk-fetch per-policy enforcement stats for the given IDs and time window."""
        if not policy_ids:
            return {}
        events = EnforcementEvent.objects.filter(policy_id__in=policy_ids, created_at__gte=since).values(
            "policy_id", "action", "user_id", "endpoint_id"
        )
        stats = defaultdict(lambda: {"violations": 0, "blocked": 0, "redacted": 0, "user_ids": set()})
        for ev in events:
            pid = ev["policy_id"]
            if pid is None:
                continue
            stats[pid]["violations"] += 1
            if ev["action"] == ACTION_BLOCK:
                stats[pid]["blocked"] += 1
            elif ev["action"] == ACTION_REDACT:
                stats[pid]["redacted"] += 1
            uid = ev["user_id"]
            if uid is not None:
                stats[pid]["user_ids"].add(("user", uid))
            else:
                eid = ev["endpoint_id"]
                if eid is not None:
                    stats[pid]["user_ids"].add(("endpoint", eid))
        result = {}
        for pid in policy_ids:
            s = stats.get(pid, {"violations": 0, "blocked": 0, "redacted": 0, "user_ids": set()})
            v = s["violations"]
            b = s["blocked"]
            r = s["redacted"]
            eff = round(((b + r) / v * 100), 1) if v else 0
            result[pid] = {
                "violations": v,
                "blocked": b,
                "redacted": r,
                "effectiveness": eff,
                "affected_users": len(s["user_ids"]),
                "avg_response": None,  # Reserved for future response-time metrics
            }
        return result

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        policy_ids = [p.id for p in (page if page is not None else queryset)]
        stats_map = {}
        if policy_ids:
            try:
                days = min(int(request.query_params.get("days", 30)), 90)
            except (ValueError, TypeError):
                days = 30
            since = timezone.now() - timedelta(days=days)
            stats_map = self._get_policy_stats_map(policy_ids, since)
        context = self.get_serializer_context()
        context["policy_stats"] = stats_map
        serializer = self.get_serializer(page if page is not None else queryset, many=True, context=context)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def get_queryset(self):
        from auth.utils import get_request_organization

        qs = super().get_queryset()
        org = get_request_organization(self.request)
        if org is not None:
            qs = qs.filter(organization=org)
        elif not (getattr(self.request, "user", None) and self.request.user.is_superuser):
            qs = qs.none()
        enabled = self.request.query_params.get("enabled")
        if enabled is not None:
            qs = qs.filter(enabled=enabled.lower() == "true")
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        severity = self.request.query_params.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        # Server-scoped filtering for MCP policies
        mcp_server = self.request.query_params.get("mcp_server")
        if mcp_server:
            qs = qs.filter(mcp_server_id=mcp_server)
        mcp_server_slug = self.request.query_params.get("mcp_server_slug")
        if mcp_server_slug:
            qs = qs.filter(mcp_server__server_slug=mcp_server_slug)
        if self.action == "list":
            policy_domain = _get_required_policy_domain(self.request)
            logger.info(
                "event=POLICY_FETCH org=%s domain=%s request_id=%s",
                getattr(org, "slug", "none"),
                policy_domain,
                _get_request_id(self.request),
            )
            qs = qs.filter(policy_domain=policy_domain)
        return qs

    def perform_create(self, serializer):
        from rest_framework.exceptions import PermissionDenied

        org = get_request_organization(self.request)
        if org is None:
            raise PermissionDenied("Organization scope is required to create policies.")
        serializer.save(organization=org)

    def _build_policy_snapshot(self, policy):
        rules = list(
            policy.rules.all()
            .order_by("-priority", "id")
            .values(
                "id",
                "name",
                "rule_type",
                "condition",
                "action",
                "redaction_config",
                "priority",
                "enabled",
                "description",
            )
        )
        return {
            "policy": {
                "id": policy.id,
                "name": policy.name,
                "code": policy.code,
                "category": policy.category,
                "severity": policy.severity,
                "description": policy.description,
                "enabled": policy.enabled,
                "priority": policy.priority,
                "metadata": policy.metadata,
                "version": policy.version,
            },
            "rules": rules,
        }

    def perform_update(self, serializer):
        from rest_framework.exceptions import PermissionDenied

        policy = serializer.instance
        if policy.is_system:
            blocked = set(serializer.validated_data.keys()) & {
                "code",
                "name",
                "policy_domain",
                "organization",
                "is_system",
            }
            if blocked:
                raise PermissionDenied(
                    f"System policies cannot change: {', '.join(sorted(blocked))}. "
                    "You may enable/disable or edit rules."
                )
            if (
                "enabled" in serializer.validated_data
                and serializer.validated_data["enabled"] is False
                and not _user_is_policy_admin(self.request)
            ):
                raise PermissionDenied("Only platform admins can disable system policies.")
        client_version = serializer.validated_data.pop("version", None)
        if client_version is not None and policy.version != client_version:
            raise ConflictError(policy, client_version)
        snapshot = self._build_policy_snapshot(policy)
        PolicyVersion.objects.create(
            policy=policy,
            version=policy.version,
            snapshot=snapshot,
            comment="",
            created_by=self.request.user if self.request.user.is_authenticated else None,
        )
        policy.version += 1
        serializer.validated_data["version"] = policy.version
        serializer.save()

    def update(self, request, *args, **kwargs):
        try:
            return super().update(request, *args, **kwargs)
        except ConflictError as e:
            return Response(
                {
                    "detail": "Policy was modified by another user. Refresh and try again.",
                    "current_version": e.policy.version,
                    "your_version": e.client_version,
                    "latest_snapshot": PolicySerializer(e.policy).data,
                },
                status=status.HTTP_409_CONFLICT,
            )

    def perform_destroy(self, instance):
        if instance.is_system:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("System policies cannot be deleted.")
        instance.delete()

    @extend_schema(
        tags=["Policies"],
        summary="List policy versions",
        description=(
            "Get version history for a policy (up to 50 most recent).\n\n"
            "Optionally pass `?version=N` to retrieve a specific version's snapshot."
        ),
        parameters=[
            *_POLICY_ID_PATH_PARAM,
            OpenApiParameter(
                name="version", type=int, required=False, description="Specific version number to retrieve"
            ),
        ],
        responses={200: PolicyVersionSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="versions")
    def versions_list_or_retrieve(self, request, pk=None):
        policy = self.get_object()
        version_param = request.query_params.get("version")
        if version_param is not None:
            try:
                v = int(version_param)
            except ValueError:
                return Response({"detail": "Invalid version number."}, status=status.HTTP_400_BAD_REQUEST)
            pv = policy.versions.filter(version=v).first()
            if not pv:
                return Response({"detail": "Version not found."}, status=status.HTTP_404_NOT_FOUND)
            return Response(PolicyVersionSerializer(pv).data)
        versions = policy.versions.all()[:50]
        return Response(PolicyVersionSerializer(versions, many=True).data)

    @extend_schema(
        tags=["Policies"],
        summary="List or create rules for a policy",
        description=(
            "**GET:** List all rules under this policy, ordered by priority (descending).\n\n"
            "**POST:** Create a new rule under this policy."
        ),
        parameters=_POLICY_ID_PATH_PARAM,
        request=RuleWriteSerializer,
        responses={
            200: RuleSerializer(many=True),
            201: RuleSerializer,
        },
    )
    @action(detail=True, methods=["get", "post"], url_path="rules")
    def rules_list_or_create(self, request, pk=None):
        policy = self.get_object()
        if request.method == "GET":
            rules = policy.rules.all().order_by("-priority", "id")
            serializer = RuleSerializer(rules, many=True)
            return Response(serializer.data)
        # POST: create rule under policy
        serializer = RuleWriteSerializer(data={**request.data, "policy": policy.id})
        serializer.is_valid(raise_exception=True)
        serializer.save(policy=policy)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Policies"],
        summary="Get policies for a device",
        description=(
            "Get all active policies assigned to a device's organization.\n\n"
            "**Authentication:** JWT required.\n\n"
            "Maps device -> device owner -> organization, then returns all enabled policies for that organization."
        ),
        parameters=[
            OpenApiParameter(
                name="device_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="UUID of the enrolled device",
                required=True,
            ),
        ],
        responses={200: PolicyListWithStatsSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="by-device")
    def by_device(self, request):
        """Excluded in AI Mesh Firewall SKU (endpoint device fleet not shipped)."""
        return Response(
            {"detail": "Device-scoped policies are not available in AI Mesh Firewall."},
            status=status.HTTP_410_GONE,
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Rules"],
        summary="List all rules",
        description=(
            "List all rules across all policies. Paginated.\n\n"
            "**Authentication:** JWT required.\n\n"
            "To list rules for a specific policy, use `GET /api/policies/{id}/rules/` instead."
        ),
    ),
    create=extend_schema(
        tags=["Rules"],
        summary="Create rule",
        description=(
            "Create a new rule. Must specify `policy` ID in the request body.\n\n"
            "**Authentication:** JWT required.\n\n"
            "**Rule types:**\n"
            '- `regex` -- Match against a regex pattern. Condition: `{"regex": "...", "field": "prompt|response|both"}`\n'
            '- `keywords` -- Match against keyword list. Condition: `{"keywords": ["word1", "word2"], "field": "prompt|response|both"}`\n'
            '- `pattern` -- Match against predefined pattern. Condition: `{"pattern": "category", "field": "prompt|response|both"}`\n\n'
            "**Actions:**\n"
            "- `block` -- Reject the request (403 Forbidden)\n"
            "- `redact` -- Replace matched text with placeholder and forward\n"
            "- `monitor` -- Log the match but allow the request\n\n"
            "**Redaction config** (for `redact` action):\n"
            '- `{"replacement": "[REDACTED]"}` -- Custom replacement text\n'
            '- `{"regex": "...", "replacement": "..."}` -- Regex-based replacement\n\n'
            "After creating rules, compile policies (`POST /api/policies/compile/`) to push updates to Gateway."
        ),
        examples=[
            OpenApiExample(
                "Block SSN in prompts (regex)",
                value={
                    "name": "Block SSN patterns",
                    "rule_type": "regex",
                    "condition": {"regex": "\\b\\d{3}-\\d{2}-\\d{4}\\b", "field": "prompt"},
                    "action": "block",
                    "priority": 10,
                    "enabled": True,
                    "description": "Blocks prompts containing Social Security Number patterns",
                    "policy": 1,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Redact credit cards (regex with redaction config)",
                value={
                    "name": "Redact credit card numbers",
                    "rule_type": "regex",
                    "condition": {"regex": "\\b\\d{4}[- ]?\\d{4}[- ]?\\d{4}[- ]?\\d{4}\\b", "field": "both"},
                    "action": "redact",
                    "redaction_config": {"replacement": "[CC_REDACTED]"},
                    "priority": 10,
                    "enabled": True,
                    "description": "Replaces credit card numbers with [CC_REDACTED] in prompts and responses",
                    "policy": 1,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Block prompt injection attempts (regex)",
                value={
                    "name": "Block ignore-instructions",
                    "rule_type": "regex",
                    "condition": {
                        "regex": "(?i)(ignore|disregard|forget)\\s+(all\\s+)?(previous|prior|above)\\s+(instructions|rules|context)",
                        "field": "prompt",
                    },
                    "action": "block",
                    "priority": 20,
                    "enabled": True,
                    "description": "Blocks common prompt injection patterns",
                    "policy": 2,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Monitor jailbreak keywords",
                value={
                    "name": "Monitor jailbreak keywords",
                    "rule_type": "keywords",
                    "condition": {
                        "keywords": ["DAN mode", "developer mode", "unrestricted mode", "jailbreak"],
                        "field": "prompt",
                    },
                    "action": "monitor",
                    "priority": 5,
                    "enabled": True,
                    "description": "Logs jailbreak attempts without blocking",
                    "policy": 3,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Redact email addresses (regex)",
                value={
                    "name": "Redact email addresses",
                    "rule_type": "regex",
                    "condition": {"regex": "\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b", "field": "both"},
                    "action": "redact",
                    "redaction_config": {"replacement": "[EMAIL_REDACTED]"},
                    "priority": 8,
                    "enabled": True,
                    "description": "Redacts email addresses in prompts and responses",
                    "policy": 1,
                },
                request_only=True,
            ),
        ],
    ),
    retrieve=extend_schema(
        tags=["Rules"],
        summary="Get rule detail",
        description="Retrieve a single rule by its ID.\n\n**Authentication:** JWT required.",
        parameters=_RULE_ID_PATH_PARAM,
    ),
    update=extend_schema(
        tags=["Rules"],
        summary="Update rule (full)",
        description="Full update of a rule. All fields are required.\n\n**Authentication:** JWT required.",
        parameters=_RULE_ID_PATH_PARAM,
    ),
    partial_update=extend_schema(
        tags=["Rules"],
        summary="Update rule (partial)",
        description=(
            "Partial update of a rule. Only include the fields you want to change.\n\n"
            "**Authentication:** JWT required.\n\n"
            "**Common operations:**\n"
            '- Enable: `{"enabled": true}`\n'
            '- Disable: `{"enabled": false}`\n'
            '- Change action: `{"action": "block"}`\n'
            '- Update regex: `{"condition": {"regex": "new_pattern", "field": "prompt"}}`'
        ),
        parameters=_RULE_ID_PATH_PARAM,
        examples=[
            OpenApiExample(
                "Enable rule",
                value={"enabled": True},
                request_only=True,
            ),
            OpenApiExample(
                "Disable rule",
                value={"enabled": False},
                request_only=True,
            ),
            OpenApiExample(
                "Change action from monitor to block",
                value={"action": "block"},
                request_only=True,
            ),
        ],
    ),
    destroy=extend_schema(
        tags=["Rules"],
        summary="Delete rule",
        description=(
            "Delete a rule.\n\n"
            "**Authentication:** JWT required.\n\n"
            "After deletion, compile policies (`POST /api/policies/compile/`) to update the Gateway."
        ),
        parameters=_RULE_ID_PATH_PARAM,
    ),
)
class RuleViewSet(ModelViewSet):
    """CRUD for rules (by id). Nested under policy for create; standalone for update/delete."""

    permission_classes = [IsAuthenticated]
    queryset = Rule.objects.all().select_related("policy").order_by("policy", "-priority", "id")

    def get_queryset(self):
        qs = super().get_queryset()
        org = get_request_organization(self.request)
        if org is not None:
            return qs.filter(policy__organization=org)
        if getattr(self.request.user, "is_superuser", False):
            return qs
        return qs.none()

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return RuleWriteSerializer
        return RuleSerializer

    def create(self, request, *args, **kwargs):
        policy_id = kwargs.get("policy_pk")
        if policy_id is None:
            policy_id = request.data.get("policy")
        if not policy_id:
            return Response(
                {"policy": "Policy id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        org = get_request_organization(request)
        policy_qs = Policy.objects.filter(pk=policy_id)
        if org is not None:
            policy_qs = policy_qs.filter(organization=org)
        elif not request.user.is_superuser:
            policy_qs = policy_qs.none()

        policy = policy_qs.first()
        if not policy:
            return Response({"policy": "Policy not found."}, status=status.HTTP_404_NOT_FOUND)
        if policy.is_system and not _user_is_policy_admin(request):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only platform admins can add rules to system policies.")
        serializer = RuleWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save(policy=policy)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        from rest_framework.exceptions import PermissionDenied

        rule = serializer.instance
        if rule.policy.is_system and not _user_is_policy_admin(self.request):
            raise PermissionDenied("Only platform admins can edit system policy rules.")
        serializer.save()

    def perform_destroy(self, instance):
        from rest_framework.exceptions import PermissionDenied

        if instance.policy.is_system and not _user_is_policy_admin(self.request):
            raise PermissionDenied("Only platform admins can delete system policy rules.")
        instance.delete()
