"""Serializers for GatewayAPIKey CRUD operations."""

import logging

from django.utils import timezone
from rest_framework import serializers

from core.models import DEFAULT_PERMISSIONS, DEFAULT_RATE_LIMIT_TPM, GatewayAPIKey

logger = logging.getLogger(__name__)


class GatewayAPIKeyCreateSerializer(serializers.Serializer):
    """Request body for POST /api/gateways/keys/ — generate a new Gateway API Key."""

    name = serializers.CharField(
        max_length=128,
        help_text="Human-readable label for the key (e.g. 'prod-rag-service').",
    )
    project_id = serializers.CharField(
        max_length=128,
        help_text="Project or tenant identifier for RBAC and billing.",
    )
    permissions = serializers.JSONField(
        required=False,
        default=DEFAULT_PERMISSIONS.copy,
        help_text=(
            'RBAC payload. Example: {"allowed_actions": ["chat", "embedding"], "denied_actions": ["fine-tuning"]}'
        ),
    )
    allowed_models = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        help_text='Model allowlist (e.g. ["gpt-4o-mini", "claude-3-sonnet"]). Empty list means all models allowed.',
    )
    rate_limit_tokens_per_minute = serializers.IntegerField(
        required=False,
        default=DEFAULT_RATE_LIMIT_TPM,
        min_value=0,
        help_text="Rate limit in Tokens Per Minute (TPM). Default: 100000.",
    )
    risk_score = serializers.FloatField(
        required=False,
        default=0.0,
        min_value=0.0,
        max_value=1.0,
        help_text="Baseline risk score (0.0 = trusted, 1.0 = highest risk).",
    )
    max_context_tokens = serializers.IntegerField(
        required=False,
        default=0,
        min_value=0,
        help_text="Max context tokens per request. 0 = unlimited.",
    )
    mcp_allowed_tools = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        help_text='MCP tool allowlist. Empty = all tools allowed.',
    )
    mcp_max_tool_calls = serializers.IntegerField(
        required=False,
        default=0,
        min_value=0,
        help_text="Max MCP tool calls per turn. 0 = unlimited.",
    )
    expires_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Optional expiry timestamp (ISO 8601). Gateway rejects expired keys.",
    )

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("Expiry timestamp must be in the future.")
        return value


class GatewayAPIKeySerializer(serializers.ModelSerializer):
    """Read-only serializer for list/retrieve — deliberately omits key_hash and owner."""

    organization_name = serializers.SerializerMethodField()
    organization_slug = serializers.SerializerMethodField()

    class Meta:
        model = GatewayAPIKey
        fields = [
            "id",
            "prefix",
            "name",
            "project_id",
            "organization_name",
            "organization_slug",
            "permissions",
            "allowed_models",
            "rate_limit_tokens_per_minute",
            "risk_score",
            "max_context_tokens",
            "mcp_allowed_tools",
            "mcp_max_tool_calls",
            "is_active",
            "expires_at",
            "last_used_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_organization_name(self, obj):
        return obj.organization.name if obj.organization else None

    def get_organization_slug(self, obj):
        return obj.organization.slug if obj.organization else None


class GatewayAPIKeyUpdateSerializer(serializers.ModelSerializer):
    """PATCH serializer — all fields optional for partial updates."""

    class Meta:
        model = GatewayAPIKey
        fields = [
            "name",
            "permissions",
            "allowed_models",
            "rate_limit_tokens_per_minute",
            "risk_score",
            "max_context_tokens",
            "mcp_allowed_tools",
            "mcp_max_tool_calls",
            "is_active",
            "expires_at",
        ]
        extra_kwargs = {
            "name": {"required": False},
            "permissions": {"required": False},
            "allowed_models": {"required": False},
            "rate_limit_tokens_per_minute": {"required": False},
            "risk_score": {"required": False},
            "max_context_tokens": {"required": False},
            "mcp_allowed_tools": {"required": False},
            "mcp_max_tool_calls": {"required": False},
            "is_active": {"required": False},
            "expires_at": {"required": False},
        }

    def validate_risk_score(self, value):
        if value is not None and not (0.0 <= value <= 1.0):
            raise serializers.ValidationError("Risk score must be between 0.0 and 1.0.")
        return value

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("Expiry timestamp must be in the future.")
        return value
