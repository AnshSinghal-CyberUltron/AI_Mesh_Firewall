from rest_framework import serializers

from module2.models import AlertFiring, AlertRule, AnomalyRule, Playbook, PlaybookRun, ThreatIntelEntry
from module2.threat_intel_projection import resolve_projection_row


class PlaybookSerializer(serializers.ModelSerializer):
    class Meta:
        model = Playbook
        fields = [
            "id",
            "organization",
            "name",
            "description",
            "steps",
            "enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]


class AlertRuleSerializer(serializers.ModelSerializer):
    playbook_name = serializers.CharField(source="playbook.name", read_only=True, default=None)

    class Meta:
        model = AlertRule
        fields = [
            "id",
            "organization",
            "name",
            "description",
            "metric",
            "operator",
            "threshold",
            "window_seconds",
            "severity",
            "enabled",
            "playbook",
            "playbook_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]


class AlertFiringSerializer(serializers.ModelSerializer):
    rule_name = serializers.CharField(source="rule.name", read_only=True)
    rule_severity = serializers.CharField(source="rule.severity", read_only=True)
    incident_id = serializers.IntegerField(source="linked_incident_id", read_only=True)

    class Meta:
        model = AlertFiring
        fields = [
            "id",
            "rule",
            "rule_name",
            "rule_severity",
            "fired_at",
            "resolved_at",
            "current_value",
            "linked_incident",
            "incident_id",
            "message",
        ]
        read_only_fields = fields


class PlaybookRunSerializer(serializers.ModelSerializer):
    playbook_name = serializers.CharField(source="playbook.name", read_only=True)

    class Meta:
        model = PlaybookRun
        fields = [
            "id",
            "playbook",
            "playbook_name",
            "trigger",
            "status",
            "result",
            "started_at",
            "finished_at",
        ]
        read_only_fields = fields


class AnomalyRuleSerializer(serializers.ModelSerializer):
    playbook_name = serializers.CharField(source="playbook.name", read_only=True, default=None)

    class Meta:
        model = AnomalyRule
        fields = [
            "id",
            "organization",
            "name",
            "scope",
            "scope_id",
            "metric",
            "z_score_threshold",
            "baseline_window_hours",
            "enabled",
            "playbook",
            "playbook_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]


class ThreatIntelEntrySerializer(serializers.ModelSerializer):
    effective_mode = serializers.SerializerMethodField()
    effective_reason = serializers.SerializerMethodField()
    projected_keyword = serializers.SerializerMethodField()

    def _live_keyword_keys(self) -> set[str] | None:
        keys = self.context.get("live_blocked_keyword_keys")
        if keys is None:
            return None
        return keys

    def _row(self, obj):
        return resolve_projection_row(obj, live_keyword_keys=self._live_keyword_keys())

    def get_effective_mode(self, obj):
        return self._row(obj).effective_mode

    def get_effective_reason(self, obj):
        return self._row(obj).effective_reason

    def get_projected_keyword(self, obj):
        return self._row(obj).keyword or ""

    class Meta:
        model = ThreatIntelEntry
        fields = [
            "id",
            "organization",
            "source",
            "threat_type",
            "indicator",
            "owasp_code",
            "confidence",
            "auto_block",
            "expires_at",
            "created_at",
            "effective_mode",
            "effective_reason",
            "projected_keyword",
        ]
        read_only_fields = ["id", "organization", "created_at"]
