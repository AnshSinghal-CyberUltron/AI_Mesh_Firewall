"""DRF serializer for LLMModelConfig CRUD operations."""

from decimal import Decimal

from rest_framework import serializers

from core.models import (
    LLMModelConfig,
    is_platform_managed_llm_model_name,
    is_platform_managed_llm_provider,
)
from core.routing_compliance import (
    SUPPORTED_COMPLIANCE_FRAMEWORKS,
    canonical_compliance_tag,
)

# Routing attributes an operator MUST state when registering a model. Defaults would
# make every model score identically, which is what made routing arbitrary before.
REQUIRED_ROUTING_FIELDS_ON_CREATE = (
    "data_sensitivity_level",
    "cost_per_1k_input_tokens",
    "latency_sla_ms",
    "routing_priority",
    "risk_score",
)


class LLMModelConfigSerializer(serializers.ModelSerializer):
    provider_display = serializers.CharField(source="get_provider_display", read_only=True)
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=True)
    api_key_set = serializers.BooleanField(read_only=True)
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

    def validate_model_name(self, value: str) -> str:
        if not value or not value.strip():
            raise serializers.ValidationError("Model name cannot be empty.")
        return value.strip()

    def validate_model_id(self, value: str) -> str:
        if not value or not value.strip():
            raise serializers.ValidationError("Model ID cannot be empty.")
        return value.strip()

    def validate_compliance_tags(self, value):
        """Canonicalize compliance tags and reject unknown frameworks.

        These are matched by the gateway's routing filter. When they were free text,
        an operator typing 'hipaa' on one model and 'HIPAA' on another produced a
        catalogue where a client asking for either got a partial match — or, if every
        model used the other casing, a spurious 'no compliant model' 403. Storing a
        canonical form makes the filter's job unambiguous.
        """
        if value in (None, ""):
            return []
        if not isinstance(value, (list, tuple)):
            raise serializers.ValidationError("Compliance tags must be a list.")
        canonical, unknown = [], []
        for raw in value:
            tag = canonical_compliance_tag(raw)
            if not tag:
                continue
            if tag not in SUPPORTED_COMPLIANCE_FRAMEWORKS:
                unknown.append(str(raw))
            elif tag not in canonical:
                canonical.append(tag)
        if unknown:
            raise serializers.ValidationError(
                f"Unsupported compliance framework(s): {', '.join(unknown)}. "
                f"Supported: {', '.join(SUPPORTED_COMPLIANCE_FRAMEWORKS)}."
            )
        return canonical

    def validate(self, attrs):
        """
        Routing configuration is COMPULSORY when adding a model.

        A model saved on bare defaults (cost 0, SLA 30000, priority 0, risk 0) is
        indistinguishable from every other default model, so the weighted scorer has
        nothing to rank on — every candidate ties and the winner falls to catalogue
        order. Requiring these up front is what makes routing meaningful at all, so
        they are mandatory on CREATE. Updates stay partial-friendly: an existing model
        already carries values, and PATCHing one field must not force a full resend.
        """
        is_create = self.instance is None
        if is_create:
            missing = [
                field for field in REQUIRED_ROUTING_FIELDS_ON_CREATE
                if attrs.get(field) is None
            ]
            if missing:
                raise serializers.ValidationError({
                    field: (
                        "Routing configuration is required when adding a model. "
                        "Without it this model cannot be ranked against the others "
                        "and routing decisions become arbitrary."
                    )
                    for field in missing
                })

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
        submitted_key = str(attrs.get("api_key") or "").strip()
        has_existing_key = bool(getattr(self.instance, "api_key_set", False))
        if provider not in {"internal", "ollama"} and not submitted_key and not has_existing_key:
            raise serializers.ValidationError(
                {"api_key": "Provider API key is required for organization-owned inference models."}
            )
        # Organization inference never uses platform environment-variable fallbacks.
        if "api_key_env_var" in attrs:
            attrs["api_key_env_var"] = ""
        return attrs

    def create(self, validated_data):
        api_key = validated_data.pop("api_key", None)
        instance = super().create(validated_data)
        if api_key is not None:
            instance.set_api_key(api_key)
            instance.save(update_fields=["encrypted_api_key", "api_key_last4", "updated_at"])
        return instance

    def update(self, instance, validated_data):
        api_key = validated_data.pop("api_key", None)
        instance = super().update(instance, validated_data)
        if api_key is not None:
            instance.set_api_key(api_key)
            instance.save(update_fields=["encrypted_api_key", "api_key_last4", "updated_at"])
        return instance
