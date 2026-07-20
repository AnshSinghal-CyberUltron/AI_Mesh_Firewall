"""Serializer for FirewallConfig singleton model."""

import math

from django.db import models as django_models
from rest_framework import serializers

from core.firewall_model_governance import (
    format_allowed_models,
    get_connected_models,
    parse_allowed_models,
    stale_allowed_models,
    validate_governance_fields,
)
from core.models import FirewallConfig


class FiniteFloatField(serializers.FloatField):
    """A FloatField that rejects IEEE-754 non-finite values (NaN, +Inf, -Inf).

    DRF's stock ``FloatField`` coerces the JSON strings ``"NaN"`` / ``"Infinity"``
    (and the bare literals Python's ``json`` accepts) into ``float('nan')`` /
    ``float('inf')``, and ``MinValueValidator`` / ``MaxValueValidator`` SILENTLY
    PASS NaN (every NaN comparison is ``False``). The non-finite value is then
    stored, and the next ``GET`` serializes the row with
    ``json.dumps(..., allow_nan=False)`` -> ``ValueError`` -> the config endpoint
    returns HTTP 500 on EVERY read (a persistent, self-inflicted DoS that bricks
    the whole settings page). Reject non-finite values at input so they can never
    be persisted.
    """

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if value is not None and not math.isfinite(value):
            raise serializers.ValidationError(
                "Must be a finite number (NaN / Infinity are not allowed)."
            )
        return value


class AllowedModelsField(serializers.Field):
    """Accept comma-separated string or JSON array; store as comma-separated text."""

    def to_representation(self, value):
        return value if value is not None else ""

    def to_internal_value(self, data):
        return format_allowed_models(parse_allowed_models(data))


class FirewallConfigSerializer(serializers.ModelSerializer):
    """
    Full FirewallConfig representation for GET/PUT API.

    Excludes internal fields (id, updated_by) from client input.
    ``updated_at`` is read-only so clients see when the config was last saved.
    """

    # Map every auto-generated model FloatField to the NaN/Inf-rejecting field
    # so a single poisoned threshold/weight can never brick the read path.
    serializer_field_mapping = {
        **serializers.ModelSerializer.serializer_field_mapping,
        django_models.FloatField: FiniteFloatField,
    }

    connected_models = serializers.SerializerMethodField()
    allowed_models_list = serializers.SerializerMethodField()
    governance_stale_models = serializers.SerializerMethodField()
    allowed_models = AllowedModelsField(required=False, allow_null=True)

    class Meta:
        model = FirewallConfig
        exclude = ("id", "updated_by")
        read_only_fields = ("updated_at", "connected_models", "allowed_models_list", "governance_stale_models")

    def get_connected_models(self, obj) -> list:
        org = self.context.get("organization")
        return get_connected_models(org)

    def get_allowed_models_list(self, obj) -> list:
        return parse_allowed_models(obj.allowed_models)

    def get_governance_stale_models(self, obj) -> list:
        org = self.context.get("organization")
        return stale_allowed_models(parse_allowed_models(obj.allowed_models), org)

    def validate_compliance_frameworks(self, value):
        """Enforce list[str]. A scalar (int/bool) would later crash
        ``set(compliance_frameworks)`` in validate() with a 500; a bare string
        would be stored as a string and pollute the gateway payload."""
        if value in (None, ""):
            return []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise serializers.ValidationError(
                'Must be a list of framework name strings (e.g. ["SOC2", "HIPAA"]).'
            )
        return value

    def validate(self, attrs):
        compliance_frameworks = attrs.get(
            "compliance_frameworks",
            getattr(self.instance, "compliance_frameworks", []),
        ) or []
        if not isinstance(compliance_frameworks, list):
            # Defensive: a previously-stored malformed value must not crash
            # validation (set() on a non-iterable scalar raises TypeError -> 500).
            compliance_frameworks = []
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

        # FULL OPERATOR CONTROL (2026-07-16): the organization owner is the SOLE
        # controller of their output-guardrail posture. The previous compliance
        # "floor" that force-rejected disabling a sensitive-data detector, setting
        # any per-detector action to "allow", or turning off security-incident
        # logging under strict frameworks (HIPAA / PCI-DSS / SOC2) is REMOVED — the
        # operator's explicit per-detector enable + action choice is honoured with
        # NO server-imposed default or override. Compliance frameworks still drive
        # the tier2_execution_mode guard above and downstream telemetry tagging;
        # they no longer override the operator's action choices.

        org = self.context.get("organization")
        # Only (re)validate model-governance fields when this request actually
        # modifies one of them. A partial PUT that touches unrelated settings
        # (e.g. §1.7 output-guardrail actions) must not be rejected because of a
        # pre-existing/stale allowed_models value the caller is not changing.
        governance_keys = {"allowed_models", "default_model", "model_isolation_enabled"}
        if governance_keys.intersection(attrs.keys()):
            allowed_raw = attrs.get(
                "allowed_models",
                getattr(self.instance, "allowed_models", ""),
            )
            default_model = attrs.get(
                "default_model",
                getattr(self.instance, "default_model", ""),
            )
            model_isolation = attrs.get(
                "model_isolation_enabled",
                getattr(self.instance, "model_isolation_enabled", True),
            )
            gov_errors = validate_governance_fields(
                organization=org,
                allowed_models=allowed_raw,
                default_model=default_model,
                model_isolation_enabled=model_isolation,
            )
            if gov_errors:
                raise serializers.ValidationError(gov_errors)

        return attrs
