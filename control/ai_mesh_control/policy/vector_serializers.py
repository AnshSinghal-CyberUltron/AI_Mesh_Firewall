"""Serializers for VectorCollectionPolicy CRUD operations."""

import logging
from typing import Any

from rest_framework import serializers

from policy.vector_models import VALID_VECTOR_OPERATIONS, VectorCollectionPolicy

logger = logging.getLogger(__name__)


class VectorCollectionPolicySerializer(serializers.ModelSerializer):
    """Read-only serializer for list/retrieve responses."""

    class Meta:
        model = VectorCollectionPolicy
        fields = [
            "id",
            "name",
            "project_id",
            "collection_name",
            "vector_db_type",
            "namespace",
            "default_action",
            "allowed_operations",
            "max_results_per_query",
            "max_query_length",
            "sensitive_fields",
            "require_context_scan",
            "block_sensitive_documents",
            "embedding_model",
            "embedding_dimension",
            "anomaly_distance_threshold",
            "enabled",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class VectorCollectionPolicyCreateSerializer(serializers.ModelSerializer):
    """Write serializer for POST (create) operations."""

    # ``project_id`` is a LEGACY tenant identifier. The gateway now keys every
    # compiled policy by ``{organization_id}::{collection_name}`` (see
    # vector_policy_sync.get_policy / vector_compiler), so a per-collection policy
    # no longer needs a project_id — the org is resolved from the caller's gateway
    # key. The model field lacks ``blank=True``, so ModelSerializer inferred
    # ``required=True, allow_blank=False`` and rejected the frontend's default
    # empty value with 400 "This field may not be blank." — which made it
    # impossible to create a policy through the UI without hand-entering an
    # arbitrary project_id (and a policy-less collection then hard-403s every
    # query as ``rag_access_denied``). Make it optional here; existing rows and
    # the org-keyed bundle already use "".
    project_id = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=128,
        help_text=(
            "Optional legacy tenant identifier. Leave blank unless pinning the "
            "policy to a specific GatewayAPIKey.project_id; policies are keyed by "
            "organization."
        ),
    )

    class Meta:
        model = VectorCollectionPolicy
        fields = [
            "name",
            "project_id",
            "collection_name",
            "vector_db_type",
            "namespace",
            "default_action",
            "allowed_operations",
            "max_results_per_query",
            "max_query_length",
            "sensitive_fields",
            "require_context_scan",
            "block_sensitive_documents",
            "embedding_model",
            "embedding_dimension",
            "anomaly_distance_threshold",
            "enabled",
            "metadata",
        ]

    def validate_allowed_operations(self, value: list[Any]) -> list[str]:
        if not isinstance(value, list):
            raise serializers.ValidationError("allowed_operations must be a list.")
        invalid = [op for op in value if op not in VALID_VECTOR_OPERATIONS]
        if invalid:
            raise serializers.ValidationError(
                f"Invalid operations: {invalid}. Valid operations: {list(VALID_VECTOR_OPERATIONS)}"
            )
        return value

    def validate_anomaly_distance_threshold(self, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise serializers.ValidationError("anomaly_distance_threshold must be between 0.0 and 1.0.")
        return value

    def validate_sensitive_fields(self, value: list[Any]) -> list[str]:
        if not isinstance(value, list):
            raise serializers.ValidationError("sensitive_fields must be a list of strings.")
        for item in value:
            if not isinstance(item, str):
                raise serializers.ValidationError("Each sensitive field must be a string.")
        return value


class VectorCollectionPolicyUpdateSerializer(serializers.ModelSerializer):
    """PATCH serializer -- all fields optional for partial updates."""

    class Meta:
        model = VectorCollectionPolicy
        fields = [
            "name",
            "namespace",
            "default_action",
            "allowed_operations",
            "max_results_per_query",
            "max_query_length",
            "sensitive_fields",
            "require_context_scan",
            "block_sensitive_documents",
            "embedding_model",
            "embedding_dimension",
            "anomaly_distance_threshold",
            "enabled",
            "metadata",
        ]
        extra_kwargs = {field: {"required": False} for field in fields}

    def validate_allowed_operations(self, value: list[Any]) -> list[str]:
        if not isinstance(value, list):
            raise serializers.ValidationError("allowed_operations must be a list.")
        invalid = [op for op in value if op not in VALID_VECTOR_OPERATIONS]
        if invalid:
            raise serializers.ValidationError(
                f"Invalid operations: {invalid}. Valid operations: {list(VALID_VECTOR_OPERATIONS)}"
            )
        return value

    def validate_anomaly_distance_threshold(self, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise serializers.ValidationError("anomaly_distance_threshold must be between 0.0 and 1.0.")
        return value
