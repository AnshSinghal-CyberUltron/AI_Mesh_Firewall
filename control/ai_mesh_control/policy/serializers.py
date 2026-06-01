from rest_framework import serializers

from .models import Policy, PolicyVersion, Rule


class RuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rule
        fields = [
            "id",
            "policy",
            "name",
            "rule_type",
            "condition",
            "action",
            "redaction_config",
            "priority",
            "enabled",
            "description",
            "pipeline_stage",
            "target_tool",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("created_at", "updated_at")


class RuleWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rule
        fields = [
            "id",
            "name",
            "rule_type",
            "condition",
            "action",
            "redaction_config",
            "priority",
            "enabled",
            "description",
            "pipeline_stage",
            "target_tool",
        ]


class PolicySerializer(serializers.ModelSerializer):
    rules = RuleSerializer(many=True, read_only=True)
    mcp_server_slug = serializers.CharField(source="mcp_server.server_slug", read_only=True, default=None)
    mcp_server_name = serializers.CharField(source="mcp_server.name", read_only=True, default=None)

    class Meta:
        model = Policy
        fields = [
            "id",
            "name",
            "code",
            "category",
            "severity",
            "description",
            "enabled",
            "is_system",
            "priority",
            "metadata",
            "policy_domain",
            "mcp_server",
            "mcp_server_slug",
            "mcp_server_name",
            "version",
            "redaction_fields",
            "allowed_user_ids",
            "allowed_agent_ids",
            "allowed_roles",
            "rules",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("created_at", "updated_at", "version", "is_system")


class PolicyListSerializer(serializers.ModelSerializer):
    rule_count = serializers.SerializerMethodField()
    mcp_server_slug = serializers.CharField(source="mcp_server.server_slug", read_only=True, default=None)
    mcp_server_name = serializers.CharField(source="mcp_server.name", read_only=True, default=None)

    class Meta:
        model = Policy
        fields = [
            "id",
            "name",
            "code",
            "category",
            "severity",
            "description",
            "enabled",
            "is_system",
            "priority",
            "rule_count",
            "metadata",
            "policy_domain",
            "mcp_server",
            "mcp_server_slug",
            "mcp_server_name",
            "version",
            "redaction_fields",
            "allowed_user_ids",
            "allowed_agent_ids",
            "allowed_roles",
            "created_at",
            "updated_at",
        ]

    def get_rule_count(self, obj):
        if not hasattr(obj, "rules"):
            return 0
        try:
            return len(obj.rules.all())
        except Exception:
            return obj.rules.count()


class PolicyListWithStatsSerializer(PolicyListSerializer):
    """Extends PolicyListSerializer with per-policy enforcement metrics."""

    violations = serializers.SerializerMethodField()
    blocked = serializers.SerializerMethodField()
    redacted = serializers.SerializerMethodField()
    effectiveness = serializers.SerializerMethodField()
    affected_users = serializers.SerializerMethodField()
    avg_response = serializers.SerializerMethodField()

    class Meta(PolicyListSerializer.Meta):
        fields = PolicyListSerializer.Meta.fields + [
            "violations",
            "blocked",
            "redacted",
            "effectiveness",
            "affected_users",
            "avg_response",
        ]

    def get_violations(self, obj):
        stats = self._get_stats(obj)
        return stats.get("violations", 0)

    def get_blocked(self, obj):
        stats = self._get_stats(obj)
        return stats.get("blocked", 0)

    def get_redacted(self, obj):
        stats = self._get_stats(obj)
        return stats.get("redacted", 0)

    def get_effectiveness(self, obj):
        stats = self._get_stats(obj)
        return stats.get("effectiveness", 0)

    def get_affected_users(self, obj):
        stats = self._get_stats(obj)
        return stats.get("affected_users", 0)

    def get_avg_response(self, obj):
        stats = self._get_stats(obj)
        return stats.get("avg_response")

    def _get_stats(self, obj):
        stats_map = self.context.get("policy_stats") or {}
        return stats_map.get(obj.id, {})


class PolicyWriteSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(
        required=False, allow_null=True, help_text="Client version for conflict check (PATCH)"
    )
    mcp_server = serializers.PrimaryKeyRelatedField(
        queryset=Policy.mcp_server.field.related_model.objects.none(),
        required=False,
        allow_null=True,
        help_text="Optional MCP server UUID to bind this policy to (MCP domain only)",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None:
            from auth.utils import get_request_organization

            org = get_request_organization(request)
            mcp_model = Policy.mcp_server.field.related_model
            if org is not None:
                self.fields["mcp_server"].queryset = mcp_model.objects.filter(organization=org)
            elif getattr(request.user, "is_superuser", False):
                self.fields["mcp_server"].queryset = mcp_model.objects.all()
            else:
                self.fields["mcp_server"].queryset = mcp_model.objects.none()

    class Meta:
        model = Policy
        fields = [
            "id",
            "name",
            "code",
            "category",
            "severity",
            "description",
            "enabled",
            "priority",
            "metadata",
            "policy_domain",
            "mcp_server",
            "version",
            "redaction_fields",
            "allowed_user_ids",
            "allowed_agent_ids",
            "allowed_roles",
        ]
        extra_kwargs = {"version": {"read_only": False}}


class PolicyVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PolicyVersion
        fields = ["id", "policy", "version", "snapshot", "comment", "created_at", "created_by"]
        read_only_fields = ["id", "policy", "version", "snapshot", "created_at", "created_by"]
