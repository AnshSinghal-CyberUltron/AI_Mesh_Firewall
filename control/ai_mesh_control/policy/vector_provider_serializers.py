"""Serializers for VectorProviderConfig CRUD operations."""

from rest_framework import serializers

from ai_mesh_shared.url_safety import (
    UnsafeProviderURLError,
    validate_safe_provider_url,
)
from policy.vector_provider_models import VectorProviderConfig


class VectorProviderConfigReadSerializer(serializers.ModelSerializer):
    """Read serializer — masks the api_key field for security."""

    api_key_set = serializers.SerializerMethodField()

    class Meta:
        model = VectorProviderConfig
        fields = [
            "id",
            "provider_type",
            "display_name",
            "connection_url",
            "api_key_set",
            "environment",
            "embedding_model",
            "is_active",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_api_key_set(self, obj) -> bool:
        """Return True if an API key is configured (never expose the key itself)."""
        return bool(obj.api_key)


class VectorProviderConfigWriteSerializer(serializers.ModelSerializer):
    """Write serializer for create and update operations."""

    api_key = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        help_text="Provider API key. Write-only — never returned in responses.",
    )

    class Meta:
        model = VectorProviderConfig
        fields = [
            "provider_type",
            "display_name",
            "connection_url",
            "api_key",
            "environment",
            "embedding_model",
            "is_active",
            "metadata",
        ]

    def validate_provider_type(self, value):
        valid = ("pinecone", "milvus", "custom")
        if value not in valid:
            raise serializers.ValidationError(f"Invalid provider_type. Must be one of: {valid}")
        return value

    def validate_connection_url(self, value):
        """
        Bundle X2 — SSRF guard on the connection URL admins post for custom
        vector providers. Previously the URL flowed straight into
        ``MilvusClient(uri=…)`` inside the gateway, letting any org admin
        coerce the gateway into requesting cloud-metadata services
        (``169.254.169.254``), loopback / RFC1918 hosts, or non-HTTP
        schemes. Validation now happens at write-time so a bad value never
        reaches Redis or the runtime resolver. The runtime resolver
        re-validates as defence in depth for rows written before this fix.
        """
        if not value:
            # Empty is legitimate for managed providers (Pinecone) that
            # rely on environment + api_key instead of a connection URL.
            return value
        try:
            return validate_safe_provider_url(value).url
        except UnsafeProviderURLError as exc:
            raise serializers.ValidationError(str(exc)) from exc
