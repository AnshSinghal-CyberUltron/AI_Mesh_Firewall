from rest_framework import serializers

from module3.models import (
    AdmissionDecision,
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
