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
