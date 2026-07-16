from rest_framework import serializers

from module3.models import (
    AdmissionDecision,
    ApiGovernanceEvent,
    ApiQuotaPolicy,
    ClusterRegistration,
    EmbeddingInspectionJob,
    ModelArtifact,
    NetworkPolicyEvent,
    PipelineRun,
    WorkloadPod,
)


class ModelArtifactSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelArtifact
        fields = [
            "id",
            "name",
            "version",
            "image_ref",
            "data_sha256",
            "model_sha256",
            "signature_digest",
            "signature_status",
            "source",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "signature_status", "created_at", "updated_at"]


class ModelArtifactCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    version = serializers.CharField(max_length=128)
    image_ref = serializers.CharField(max_length=512)
    data_sha256 = serializers.CharField(max_length=64, required=False, allow_blank=True)
    model_sha256 = serializers.CharField(max_length=64, required=False, allow_blank=True)
    signature_digest = serializers.CharField(required=False, allow_blank=True)
    source = serializers.ChoiceField(choices=["ci", "manual"], default="manual")


class VerifyArtifactSerializer(serializers.Serializer):
    artifact_id = serializers.IntegerField(required=False)
    image_ref = serializers.CharField(max_length=512, required=False, allow_blank=True)
    signature_digest = serializers.CharField(required=False, allow_blank=True)
    data_sha256 = serializers.CharField(max_length=64, required=False, allow_blank=True)
    model_sha256 = serializers.CharField(max_length=64, required=False, allow_blank=True)


class ClusterHeartbeatSerializer(serializers.Serializer):
    cluster_name = serializers.CharField(max_length=255)
    k8s_version = serializers.CharField(max_length=32, required=False, allow_blank=True)
    cilium_enabled = serializers.BooleanField(default=True)
    status = serializers.ChoiceField(
        choices=["healthy", "degraded", "offline"], default="healthy"
    )
    pods = serializers.ListField(child=serializers.DictField(), required=False, default=list)


class NetworkEventIngestSerializer(serializers.Serializer):
    cluster_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    layer = serializers.ChoiceField(choices=["ebpf", "envoy"])
    action = serializers.ChoiceField(choices=["allow", "drop"])
    source_ref = serializers.CharField(max_length=512)
    dest_ref = serializers.CharField(max_length=512)
    reason = serializers.CharField(required=False, allow_blank=True)


class EmbeddingInspectionIngestSerializer(serializers.Serializer):
    collection = serializers.CharField(max_length=255, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=["queued", "clean", "quarantined"])
    anomaly_score = serializers.FloatField(required=False, default=0.0)
    payload_hash = serializers.CharField(max_length=64, required=False, allow_blank=True)
    quarantine_reason = serializers.CharField(required=False, allow_blank=True)


class ApiQuotaPolicySerializer(serializers.ModelSerializer):
    tokens_minute_used = serializers.SerializerMethodField()
    tokens_day_used = serializers.SerializerMethodField()

    class Meta:
        model = ApiQuotaPolicy
        fields = [
            "id",
            "tenant_id",
            "environment",
            "tokens_per_minute",
            "tokens_per_day",
            "enabled",
            "denied_paths",
            "tokens_minute_used",
            "tokens_day_used",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "tokens_minute_used", "tokens_day_used"]

    def get_tokens_minute_used(self, obj):
        usage = getattr(obj, "usage", None)
        return int(getattr(usage, "tokens_minute", 0) or 0)

    def get_tokens_day_used(self, obj):
        usage = getattr(obj, "usage", None)
        return int(getattr(usage, "tokens_day", 0) or 0)


class ApiQuotaPolicyWriteSerializer(serializers.Serializer):
    tenant_id = serializers.CharField(max_length=128, required=False)
    environment = serializers.ChoiceField(
        choices=["dev", "staging", "prod"], required=False, default="prod"
    )
    tokens_per_minute = serializers.IntegerField(min_value=0, required=False, default=1000)
    tokens_per_day = serializers.IntegerField(min_value=0, required=False, default=100_000)
    enabled = serializers.BooleanField(required=False, default=True)
    denied_paths = serializers.ListField(
        child=serializers.CharField(max_length=512), required=False, default=list
    )

    def validate(self, attrs):
        # POST creates need tenant_id; PATCH may omit.
        if not self.partial and not attrs.get("tenant_id"):
            raise serializers.ValidationError({"tenant_id": "This field is required."})
        return attrs


class ApiGovernanceEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiGovernanceEvent
        fields = [
            "id",
            "action",
            "tenant_id",
            "environment",
            "estimated_tokens",
            "path",
            "reason",
            "source",
            "created_at",
        ]
        read_only_fields = fields


class GovernanceEventIngestSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["allow", "deny"])
    tenant_id = serializers.CharField(max_length=128, required=False, allow_blank=True)
    environment = serializers.CharField(max_length=16, required=False, allow_blank=True)
    estimated_tokens = serializers.IntegerField(required=False, default=0, min_value=0)
    path = serializers.CharField(max_length=512, required=False, allow_blank=True)
    reason = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(max_length=64, required=False, default="envoy_ext_authz")


class QuotaUsageIngestSerializer(serializers.Serializer):
    tenant_id = serializers.CharField(max_length=128)
    environment = serializers.ChoiceField(choices=["dev", "staging", "prod"], default="prod")
    tokens = serializers.IntegerField(min_value=1)

