"""
VectorProviderConfig model for organisation-level vector DB provider credentials.

Stores provider API keys, connection URLs, and configuration per organization.
On save/delete, signals push the config to Redis for zero-latency gateway resolution.

Credential hierarchy (gateway resolves in this order):
    1. Per-request user-supplied credentials (highest priority)
    2. Organisation VectorProviderConfig from Redis
    3. Gateway environment variable defaults (lowest priority)
"""

import uuid

from django.db import models

from policy.encrypted_fields import EncryptedCharField

VECTOR_PROVIDER_CHOICES = [
    ("pinecone", "Pinecone"),
    ("milvus", "Milvus"),
    ("chroma", "Chroma"),
    ("custom", "Custom"),
]


class VectorProviderConfig(models.Model):
    """
    Organisation-level vector DB provider configuration.

    Admins set provider API keys and connection URLs here.
    The gateway resolves credentials from this config via Redis,
    falling back to env-var defaults when no org config exists.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="vector_provider_configs",
    )
    provider_type = models.CharField(
        max_length=32,
        choices=VECTOR_PROVIDER_CHOICES,
        help_text="Vector database provider (pinecone, milvus, custom).",
    )
    display_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Human-readable label for this provider configuration.",
    )
    connection_url = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Connection URL (e.g. URI for Milvus, endpoint URL for custom providers).",
    )
    api_key = EncryptedCharField(
        max_length=512,
        blank=True,
        default="",
        help_text=(
            "Provider API key (Pinecone API key, Milvus token, etc.). "
            "Encrypted at rest via Fernet (see policy.encrypted_fields). "
            "Plaintext is only materialised in-process when building the "
            "Redis bundle for the gateway."
        ),
    )
    environment = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Provider environment (e.g. Pinecone environment like 'us-east-1').",
    )
    embedding_model = models.CharField(
        max_length=128,
        blank=True,
        default="text-embedding-3-small",
        help_text="Default embedding model for this provider.",
    )
    embedding_api_key = EncryptedCharField(
        max_length=512,
        blank=True,
        default="",
        help_text=(
            "BYOK key for the EXTERNAL embedding provider (e.g. OpenRouter / "
            "OpenAI-compatible), used when embedding_model routes through litellm "
            "rather than the vector DB's own inference. Encrypted at rest via "
            "Fernet; only materialised in-process when building the Redis bundle. "
            "Falls back to gateway-environment credentials when empty."
        ),
    )
    reranker_model = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=(
            "Per-org reranker model (e.g. Pinecone-hosted 'bge-reranker-v2-m3'). "
            "When set, retrieved documents are reranked by the provider's hosted "
            "rerank API before guardrail scoring. Empty disables reranking."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this provider config is active.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional provider-specific settings.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "policy"
        ordering = ["-created_at"]
        verbose_name = "Vector Provider Config"
        verbose_name_plural = "Vector Provider Configs"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "provider_type"],
                name="unique_org_vector_provider",
            ),
        ]

    def __str__(self) -> str:
        org_name = getattr(self.organization, "name", "?")
        return f"{org_name} / {self.get_provider_type_display()} [{'active' if self.is_active else 'disabled'}]"

    def build_redis_payload(self) -> dict:
        """Build the payload cached in Redis for gateway credential resolution."""
        return {
            "id": str(self.id),
            "organization_id": self.organization_id,
            "provider_type": self.provider_type,
            "display_name": self.display_name,
            "connection_url": self.connection_url,
            "api_key": self.api_key,
            "environment": self.environment,
            "embedding_model": self.embedding_model,
            "embedding_api_key": self.embedding_api_key,
            "reranker_model": self.reranker_model,
            "is_active": self.is_active,
            "metadata": self.metadata,
        }
