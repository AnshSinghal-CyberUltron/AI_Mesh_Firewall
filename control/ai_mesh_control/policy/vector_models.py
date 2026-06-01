"""
VectorCollectionPolicy model for RAG Data Protection.

Defines access control policies for vector DB collections, enforcing
namespace isolation, collection-level RBAC, content filtering, and
embedding anomaly detection thresholds.

Each policy is scoped to a (project_id, collection_name, vector_db_type) tuple.
On save/delete, signals trigger recompilation and Redis sync for
zero-latency enforcement in the Gateway Data Plane.
"""

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

VECTOR_DB_TYPE_CHOICES = [
    ("pinecone", "Pinecone"),
    ("milvus", "Milvus"),
    ("custom", "Custom"),
]

VECTOR_ACCESS_ACTION_CHOICES = [
    ("allow", "Allow"),
    ("deny", "Deny"),
    ("monitor", "Monitor"),
]

VALID_VECTOR_OPERATIONS = ("query", "insert", "update", "delete")


class VectorCollectionPolicy(models.Model):
    """
    Access control policy for a vector DB collection.

    Enforces namespace isolation, collection-level RBAC, content filtering,
    and embedding anomaly detection for RAG workloads.

    Redis Sync: On save/delete, a debounced Celery task recompiles all
    enabled vector policies into a bundle at ``vector:policies:compiled``
    and publishes a notification on the ``vector_policy_updates`` channel.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="vector_policies",
    )
    name = models.CharField(
        max_length=255,
        help_text="Human-readable policy label.",
    )
    project_id = models.CharField(
        max_length=128,
        db_index=True,
        help_text="Tenant identifier (matches GatewayAPIKey.project_id).",
    )
    collection_name = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Logical collection name (namespaced by project_id at runtime).",
    )
    vector_db_type = models.CharField(
        max_length=32,
        choices=VECTOR_DB_TYPE_CHOICES,
        default="pinecone",
    )
    namespace = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Sub-namespace within collection (Pinecone/Milvus partition key).",
    )

    default_action = models.CharField(
        max_length=16,
        choices=VECTOR_ACCESS_ACTION_CHOICES,
        default="deny",
    )
    allowed_operations = models.JSONField(
        default=list,
        blank=True,
        help_text='Permitted operations, e.g. ["query", "insert", "update", "delete"].',
    )
    max_results_per_query = models.PositiveIntegerField(
        default=10,
        help_text="Maximum documents returned per query.",
    )
    max_query_length = models.PositiveIntegerField(
        default=2000,
        help_text="Maximum characters in query text.",
    )

    sensitive_fields = models.JSONField(
        default=list,
        blank=True,
        help_text="Document metadata fields to scan for PII before returning.",
    )
    require_context_scan = models.BooleanField(
        default=True,
        help_text="Scan retrieved documents for indirect prompt injection.",
    )
    block_sensitive_documents = models.BooleanField(
        default=True,
        help_text="Block documents containing PII/secrets from retrieval results.",
    )

    embedding_model = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Required embedding model for this collection.",
    )
    embedding_dimension = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Expected embedding vector dimension.",
    )
    anomaly_distance_threshold = models.FloatField(
        default=0.85,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Cosine distance threshold; results above this are flagged as anomalous.",
    )

    enabled = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "policy"
        ordering = ["-created_at"]
        verbose_name = "Vector Collection Policy"
        verbose_name_plural = "Vector Collection Policies"
        constraints = [
            # Bundle X1 fix: include `organization` so two tenants can each
            # own the same (project_id, collection_name, vector_db_type)
            # triplet without colliding at the DB layer. Without this,
            # Org B was permanently blocked from configuring a policy
            # whose triplet already existed for Org A, and the unique
            # violation error leaked Org A's policy existence to Org B.
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "project_id",
                    "collection_name",
                    "vector_db_type",
                ],
                name="unique_org_project_collection_vdb",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}/{self.collection_name} ({self.vector_db_type}) [{self.default_action}]"

    def build_redis_payload(self) -> dict:
        """Build the payload cached in the compiled Redis bundle for gateway consumption."""
        return {
            "id": str(self.id),
            "name": self.name,
            "project_id": self.project_id,
            "collection_name": self.collection_name,
            "vector_db_type": self.vector_db_type,
            "namespace": self.namespace,
            "default_action": self.default_action,
            "allowed_operations": self.allowed_operations,
            "max_results_per_query": self.max_results_per_query,
            "max_query_length": self.max_query_length,
            "sensitive_fields": self.sensitive_fields,
            "require_context_scan": self.require_context_scan,
            "block_sensitive_documents": self.block_sensitive_documents,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "anomaly_distance_threshold": self.anomaly_distance_threshold,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }
