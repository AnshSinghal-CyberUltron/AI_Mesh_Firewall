"""DRF serializer for LLMModelConfig CRUD operations."""

from decimal import Decimal

from rest_framework import serializers

from core.models import (
    LLMModelConfig,
    is_platform_managed_llm_model_name,
    is_platform_managed_llm_provider,
    is_reserved_inference_model_name,
)


class LLMModelConfigSerializer(serializers.ModelSerializer):
    provider_display = serializers.CharField(source="get_provider_display", read_only=True)
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=True)
    api_key_set = serializers.SerializerMethodField()
    api_key_last4 = serializers.CharField(read_only=True)
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
            "api_key",
            "api_key_set",
            "api_key_last4",
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

    def get_api_key_set(self, obj) -> bool:
        """Usability-aware: undecryptable blobs must read as disconnected (matches gateway Redis sync)."""
        return obj.has_usable_api_key()

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

        provider = str(attrs.get("provider") or getattr(self.instance, "provider", "")).strip().lower()
        model_name = str(attrs.get("model_name") or getattr(self.instance, "model_name", "")).strip()
        if getattr(self.instance, "is_platform_managed", False):
            raise serializers.ValidationError(
                "This model is managed by ZeroShield and cannot be changed from the console."
            )
        if is_platform_managed_llm_provider(provider) or is_platform_managed_llm_model_name(model_name):
            raise serializers.ValidationError(
                "ZeroShield guard models are platform-managed and cannot be added or edited here."
            )
        model_id = str(attrs.get("model_id") or getattr(self.instance, "model_id", "")).strip()
        will_be_active = attrs.get("is_active", getattr(self.instance, "is_active", True))
        if will_be_active and is_reserved_inference_model_name(model_name, model_id):
            raise serializers.ValidationError(
                "This model uses a reserved Bedrock foundation ID and cannot be activated for "
                "organization inference. Connect a user-facing alias (for example bedrock-llama-3) "
                "with a non-reserved LiteLLM model id instead."
            )
        submitted_key = str(attrs.get("api_key") or "").strip()
        has_existing_key = bool(self.instance and self.instance.has_usable_api_key())
        if provider == "aws_bedrock":
            # Development default: gateway .env AWS credentials (no per-model API key required).
            attrs["api_key_env_var"] = "AWS_ACCESS_KEY_ID"
        elif provider not in {"internal", "ollama"} and not submitted_key and not has_existing_key:
            raise serializers.ValidationError(
                {"api_key": "Provider API key is required for organization-owned inference models."}
            )
        else:
            # Organization inference never uses platform environment-variable fallbacks.
            if "api_key_env_var" in attrs:
                attrs["api_key_env_var"] = ""
        return attrs

    def create(self, validated_data):
        api_key = validated_data.pop("api_key", None)
        provider = str(validated_data.get("provider", "")).strip().lower()
        instance = super().create(validated_data)
        if provider == "aws_bedrock":
            if api_key:
                instance.set_api_key(api_key)
            else:
                instance.set_api_key("")
            instance.save(update_fields=["encrypted_api_key", "api_key_last4", "updated_at"])
        elif api_key is not None:
            instance.set_api_key(api_key)
            instance.save(update_fields=["encrypted_api_key", "api_key_last4", "updated_at"])
        return instance

    def update(self, instance, validated_data):
        api_key = validated_data.pop("api_key", None)
        provider = str(validated_data.get("provider", instance.provider or "")).strip().lower()
        instance = super().update(instance, validated_data)
        if provider == "aws_bedrock":
            if api_key is not None:
                instance.set_api_key(api_key or "")
            else:
                instance.set_api_key("")
            instance.save(update_fields=["encrypted_api_key", "api_key_last4", "updated_at"])
        elif api_key is not None:
            instance.set_api_key(api_key)
            instance.save(update_fields=["encrypted_api_key", "api_key_last4", "updated_at"])
        return instance
