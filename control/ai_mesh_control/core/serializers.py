"""Serializers for core models (Agent, Endpoint, KillSwitch)."""

from rest_framework import serializers

from core.models import AGENT_TYPE_CHOICES, Agent, Endpoint, KillSwitch


class AgentRegisterSerializer(serializers.Serializer):
    """Request body for POST /api/agents/register/."""

    agent_id = serializers.UUIDField(required=False, allow_null=True)
    agent_type = serializers.ChoiceField(choices=[c[0] for c in AGENT_TYPE_CHOICES])
    name = serializers.CharField(max_length=255)
    endpoint_identifier = serializers.CharField(max_length=255, required=False, allow_blank=True)
    endpoint_id = serializers.IntegerField(required=False, allow_null=True)
    user_id = serializers.IntegerField(required=False, allow_null=True)
    organization_id = serializers.IntegerField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False, default=dict)
    endpoint_username = serializers.CharField(required=False, allow_blank=True, max_length=255)


class AgentTelemetrySerializer(serializers.Serializer):
    """Request body for POST /api/agents/telemetry/."""

    agent_id = serializers.UUIDField()
    endpoint_id = serializers.IntegerField(required=False, allow_null=True)
    os = serializers.CharField(required=False, allow_blank=True)
    agent_version = serializers.CharField(required=False, allow_blank=True)
    threats_24h = serializers.JSONField(required=False, allow_null=True)  # number (count) or list of IDs
    active_copilots = serializers.ListField(child=serializers.CharField(), required=False, allow_null=True)
    detected_services = serializers.ListField(child=serializers.CharField(), required=False, allow_null=True)
    cpu_usage = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=100)
    memory_usage = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=100)
    process_count = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    high_load = serializers.BooleanField(required=False, allow_null=True)
    file_access_events = serializers.ListField(child=serializers.DictField(), required=False, allow_null=True)
    clipboard_metadata = serializers.DictField(required=False, allow_null=True)
    proxy_enabled = serializers.BooleanField(required=False, allow_null=True)
    endpoint_username = serializers.CharField(required=False, allow_blank=True, max_length=255)
    # Gateway-only (stored in Agent.metadata when agent_type=gateway)
    total_requests = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    blocked = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    allowed = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    avg_latency_ms = serializers.FloatField(required=False, allow_null=True, min_value=0)
    active_connections = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    rules_applied = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    location = serializers.CharField(required=False, allow_blank=True)


class EndpointSummarySerializer(serializers.ModelSerializer):
    """Minimal endpoint for nested in agent list."""

    class Meta:
        model = Endpoint
        fields = ("id", "identifier", "name", "status", "last_seen_at", "metadata")


class EndpointListSerializer(serializers.ModelSerializer):
    """Endpoint for GET /api/endpoints/ with flattened telemetry metadata for dashboard."""

    os = serializers.SerializerMethodField()
    agent_version = serializers.SerializerMethodField()
    threats_24h = serializers.SerializerMethodField()
    active_copilots = serializers.SerializerMethodField()
    detected_services = serializers.SerializerMethodField()
    cpu_usage = serializers.SerializerMethodField()
    memory_usage = serializers.SerializerMethodField()
    primary_user_id = serializers.SerializerMethodField()
    primary_user_display = serializers.SerializerMethodField()
    endpoint_username = serializers.SerializerMethodField()

    class Meta:
        model = Endpoint
        fields = (
            "id",
            "identifier",
            "name",
            "status",
            "last_seen_at",
            "os",
            "agent_version",
            "threats_24h",
            "active_copilots",
            "detected_services",
            "cpu_usage",
            "memory_usage",
            "primary_user_id",
            "primary_user_display",
            "endpoint_username",
        )

    def _meta(self, obj, key, default=None):
        return (obj.metadata or {}).get(key, default)

    def get_os(self, obj):
        return self._meta(obj, "os", "")

    def get_agent_version(self, obj):
        return self._meta(obj, "agent_version", "")

    def get_threats_24h(self, obj):
        # Prefer agent-reported value; else use backend-derived count from EnforcementEvent (last 24h)
        agent_value = self._meta(obj, "threats_24h")
        if agent_value is not None:
            return agent_value
        return self.context.get("threats_24h_by_endpoint", {}).get(obj.id, 0)

    def get_active_copilots(self, obj):
        return self._meta(obj, "active_copilots") or []

    def get_detected_services(self, obj):
        return self._meta(obj, "detected_services") or []

    def get_cpu_usage(self, obj):
        return self._meta(obj, "cpu_usage")

    def get_memory_usage(self, obj):
        return self._meta(obj, "memory_usage")

    def get_primary_user_id(self, obj):
        first = next(iter(obj.agents.all()), None)
        return first.user_id if first else None

    def get_primary_user_display(self, obj):
        """
        Return a human-friendly name for the primary user associated with this endpoint.
        Uses the most recently updated agent's user_id to resolve the Django User.
        """
        first = next(iter(obj.agents.all()), None)
        user_id = first.user_id if first else None
        if not user_id:
            return None
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None
        full = getattr(user, "get_full_name", lambda: "")() or ""
        if full.strip():
            return full
        if getattr(user, "username", ""):
            return user.username
        if getattr(user, "email", ""):
            return user.email
        return f"User {user.id}"

    def get_endpoint_username(self, obj):
        return (obj.metadata or {}).get("endpoint_username") or ""


class AgentListSerializer(serializers.ModelSerializer):
    """Agent for GET /api/agents/ list response."""

    agent_id = serializers.UUIDField(source="id", read_only=True)
    endpoint_id = serializers.SerializerMethodField()
    endpoint_summary = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = (
            "agent_id",
            "agent_type",
            "name",
            "endpoint_id",
            "user_id",
            "status",
            "metadata",
            "created_at",
            "updated_at",
            "endpoint_summary",
        )

    def get_endpoint_id(self, obj):
        return obj.endpoint_id if obj.endpoint_id else None

    def get_endpoint_summary(self, obj):
        if not obj.endpoint_id:
            return None
        return EndpointSummarySerializer(obj.endpoint).data


class KillSwitchSerializer(serializers.ModelSerializer):
    """Full KillSwitch representation for list/detail responses."""

    activated_by_username = serializers.SerializerMethodField()

    class Meta:
        model = KillSwitch
        fields = (
            "id",
            "model_name",
            "api_key_prefix",
            "is_active",
            "action",
            "fallback_model",
            "reason",
            "activated_by",
            "activated_by_username",
            "activated_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "activated_by", "activated_at", "created_at", "updated_at")

    def get_activated_by_username(self, obj: KillSwitch) -> str | None:
        if obj.activated_by:
            return obj.activated_by.username
        return None


class KillSwitchCreateSerializer(serializers.ModelSerializer):
    """Create/update a KillSwitch entry."""

    class Meta:
        model = KillSwitch
        fields = ("model_name", "api_key_prefix", "action", "fallback_model", "reason")

    def validate(self, attrs: dict) -> dict:
        if attrs.get("action") == "reroute" and not attrs.get("fallback_model"):
            raise serializers.ValidationError(
                {"fallback_model": "Fallback model is required when action is 'reroute'."}
            )
        model_name = (attrs.get("model_name") or "").strip()
        api_key_prefix = (attrs.get("api_key_prefix") or "").strip()
        if model_name == KillSwitch.SCOPE_GLOBAL:
            if api_key_prefix:
                raise serializers.ValidationError(
                    {
                        "api_key_prefix": (
                            "Org-wide emergency scope cannot use an API key prefix — "
                            "Redis uses a single global key per organization."
                        )
                    }
                )
            if attrs.get("action") == "reroute":
                raise serializers.ValidationError(
                    {
                        "action": (
                            "Global kill-switch only supports disable at the gateway; "
                            "reroute is not applied for org-wide scope."
                        )
                    }
                )
        request = self.context.get("request")
        if request and model_name and model_name != KillSwitch.SCOPE_GLOBAL:
            org = getattr(getattr(request.user, "profile", None), "organization", None)
            if org:
                from core.models import LLMModelConfig

                if not LLMModelConfig.objects.filter(
                    organization=org,
                    model_name=model_name,
                    is_active=True,
                ).exists():
                    attrs["_model_name_warning"] = (
                        f'"{model_name}" is not an active connected model for this org. '
                        "Use the registered model_name from Model Connections (not LiteLLM model_id)."
                    )
        return attrs


class KillSwitchActivateSerializer(serializers.Serializer):
    """Request body for activate/deactivate actions."""

    reason = serializers.CharField(required=False, allow_blank=True, default="")


# ── ModelState serializers ─────────────────────────────────────────────

class ModelStateSerializer(serializers.ModelSerializer):
    """Full ModelState representation."""

    class Meta:
        from core.models import ModelState
        model = ModelState
        fields = (
            "id",
            "model_name",
            "status",
            "risk_score",
            "threshold",
            "action",
            "fallback_model",
            "isolation_reason",
            "isolated_at",
            "isolated_until",
            "cooldown_seconds",
            "last_updated",
            "created_at",
        )
        read_only_fields = ("id", "risk_score", "isolated_at", "last_updated", "created_at")


class ModelStateUpdateSerializer(serializers.Serializer):
    """Update threshold/action/fallback configuration for a model."""

    threshold = serializers.FloatField(min_value=0, max_value=100, required=False)
    action = serializers.ChoiceField(choices=["block", "reroute", "alert"], required=False)
    fallback_model = serializers.CharField(required=False, allow_blank=True)
    cooldown_seconds = serializers.IntegerField(min_value=30, max_value=86400, required=False)

    def validate(self, attrs):
        if attrs.get("action") == "reroute" and not attrs.get("fallback_model"):
            raise serializers.ValidationError(
                {"fallback_model": "Fallback model is required for reroute action."}
            )
        return attrs


class ModelIsolateSerializer(serializers.Serializer):
    """Request body for manually isolating a model."""

    model_name = serializers.CharField(required=True)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    action = serializers.ChoiceField(choices=["block", "reroute", "alert"], default="block")
    fallback_model = serializers.CharField(required=False, allow_blank=True, default="")
    cooldown_seconds = serializers.IntegerField(min_value=30, max_value=86400, default=300)


class KillSwitchAuditLogSerializer(serializers.ModelSerializer):
    """Read-only audit log for kill-switch events."""

    class Meta:
        from core.models import KillSwitchAuditLog
        model = KillSwitchAuditLog
        fields = (
            "id",
            "event",
            "model_name",
            "risk_score",
            "threshold",
            "action",
            "reason",
            "request_id",
            "metadata",
            "triggered_by",
            "timestamp",
        )
