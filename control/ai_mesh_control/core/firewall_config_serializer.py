"""Serializer for FirewallConfig singleton model."""

from rest_framework import serializers

from core.models import FirewallConfig


class FirewallConfigSerializer(serializers.ModelSerializer):
    """
    Full FirewallConfig representation for GET/PUT API.

    Excludes internal fields (id, updated_by) from client input.
    ``updated_at`` is read-only so clients see when the config was last saved.
    """

    class Meta:
        model = FirewallConfig
        exclude = ("id", "updated_by")
        read_only_fields = ("updated_at",)

    def validate(self, attrs):
        compliance_frameworks = attrs.get(
            "compliance_frameworks",
            getattr(self.instance, "compliance_frameworks", []),
        ) or []
        tier2_execution_mode = attrs.get(
            "tier2_execution_mode",
            getattr(self.instance, "tier2_execution_mode", "sync_pre_llm"),
        )
        strict_frameworks = {"HIPAA", "PCI-DSS"}
        if tier2_execution_mode == "async_post_llm" and strict_frameworks.intersection(set(compliance_frameworks)):
            raise serializers.ValidationError(
                {
                    "tier2_execution_mode": (
                        "async_post_llm is not allowed when strict compliance frameworks "
                        f"are enabled ({', '.join(sorted(strict_frameworks))})."
                    )
                }
            )
        return attrs
