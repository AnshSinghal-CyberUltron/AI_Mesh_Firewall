"""DRF serializer for LLMModelConfig CRUD operations."""

from decimal import Decimal

from rest_framework import serializers

from core.models import LLMModelConfig


class LLMModelConfigSerializer(serializers.ModelSerializer):
    provider_display = serializers.CharField(source="get_provider_display", read_only=True)
    cost_per_1k_input_tokens = serializers.DecimalField(
        max_digits=10,
        decimal_places=6,
        required=False,
        allow_null=True,
        min_value=Decimal("0"),
    )
    cost_per_1k_output_tokens = serializers.DecimalField(
        max_digits=10,
        decimal_places=6,
        required=False,
        allow_null=True,
        min_value=Decimal("0"),
    )
    latency_sla_ms = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    routing_priority = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    rate_limit_rpm = serializers.IntegerField(required=False, allow_null=True, min_value=0)

    class Meta:
        model = LLMModelConfig
        fields = [
            "id",
            "provider",
            "provider_display",
            "model_name",
            "model_id",
            "api_key_env_var",
            "api_base",
            "region",
            "is_active",
            "data_sensitivity_level",
            "compliance_tags",
            "cost_per_1k_input_tokens",
            "cost_per_1k_output_tokens",
            "latency_sla_ms",
            "risk_score",
            "routing_priority",
            "rate_limit_rpm",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_model_name(self, value: str) -> str:
        if not value or not value.strip():
            raise serializers.ValidationError("Model name cannot be empty.")
        return value.strip()

    def validate_model_id(self, value: str) -> str:
        if not value or not value.strip():
            raise serializers.ValidationError("Model ID cannot be empty.")
        return value.strip()

    def validate(self, attrs):
        """
        Keep the endpoint backward-compatible with clients that send null for
        optional routing/cost fields by coercing nulls to model defaults.
        """
        null_to_default = {
            "cost_per_1k_input_tokens": Decimal("0"),
            "cost_per_1k_output_tokens": Decimal("0"),
            "latency_sla_ms": 30000,
            "routing_priority": 0,
            "rate_limit_rpm": 0,
        }
        for field, default_value in null_to_default.items():
            if attrs.get(field) is None:
                attrs[field] = default_value
        return attrs
