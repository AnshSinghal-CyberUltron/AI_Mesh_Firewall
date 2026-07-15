from django.db import models


class ModelArtifact(models.Model):
    """Signed container / model artifact in the LLMOps registry."""

    SIGNATURE_STATUS_CHOICES = [
        ("unsigned", "Unsigned"),
        ("pending", "Pending"),
        ("valid", "Valid"),
        ("invalid", "Invalid"),
    ]
    SOURCE_CHOICES = [
        ("ci", "CI/CD"),
        ("manual", "Manual"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="model_artifacts",
    )
    name = models.CharField(max_length=255)
    version = models.CharField(max_length=128)
    image_ref = models.CharField(max_length=512)
    data_sha256 = models.CharField(max_length=64, blank=True)
    model_sha256 = models.CharField(max_length=64, blank=True)
    # Cosign detached signatures (base64 / cosign-blob:…) exceed 512 chars.
    signature_digest = models.TextField(blank=True)
    signature_status = models.CharField(
        max_length=16,
        choices=SIGNATURE_STATUS_CHOICES,
        default="unsigned",
        db_index=True,
    )
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default="manual")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        unique_together = [("organization", "name", "version")]

    def __str__(self):
        return f"{self.name}:{self.version}"


class PipelineRun(models.Model):
    """CI/CD pipeline run linked to an artifact."""

    STATUS_CHOICES = [
        ("running", "Running"),
        ("success", "Success"),
        ("failed", "Failed"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="pipeline_runs",
    )
    artifact = models.ForeignKey(
        ModelArtifact,
        on_delete=models.CASCADE,
        related_name="pipeline_runs",
    )
    commit_sha = models.CharField(max_length=64, blank=True)
    workflow = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="running")
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.workflow or 'pipeline'} ({self.status})"


class AdmissionDecision(models.Model):
    """FastAPI admission gatekeeper audit log."""

    RESULT_CHOICES = [
        ("allow", "Allow"),
        ("deny", "Deny"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="admission_decisions",
    )
    artifact = models.ForeignKey(
        ModelArtifact,
        on_delete=models.CASCADE,
        related_name="admission_decisions",
    )
    result = models.CharField(max_length=8, choices=RESULT_CHOICES, db_index=True)
    reason = models.TextField(blank=True)
    verified_by = models.CharField(max_length=64, default="gateway")
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.artifact_id} {self.result}"


class ClusterRegistration(models.Model):
    """Registered Kubernetes cluster for Module 3.2."""

    STATUS_CHOICES = [
        ("healthy", "Healthy"),
        ("degraded", "Degraded"),
        ("offline", "Offline"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="k8s_clusters",
    )
    name = models.CharField(max_length=255)
    k8s_version = models.CharField(max_length=32, blank=True)
    cilium_enabled = models.BooleanField(default=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="healthy")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("organization", "name")]

    def __str__(self):
        return self.name


class WorkloadPod(models.Model):
    """AI workload pod with sidecar and mTLS posture."""

    WORKLOAD_TYPE_CHOICES = [
        ("model", "Model"),
        ("vector_db", "Vector DB"),
        ("agent", "Agent"),
    ]
    MTLS_STATUS_CHOICES = [
        ("healthy", "Healthy"),
        ("degraded", "Degraded"),
        ("missing", "Missing"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="workload_pods",
    )
    cluster = models.ForeignKey(
        ClusterRegistration,
        on_delete=models.CASCADE,
        related_name="pods",
    )
    namespace = models.CharField(max_length=128)
    pod_name = models.CharField(max_length=255)
    workload_type = models.CharField(max_length=16, choices=WORKLOAD_TYPE_CHOICES, default="model")
    labels = models.JSONField(default=dict, blank=True)
    sidecar_attached = models.BooleanField(default=False)
    mtls_status = models.CharField(
        max_length=16,
        choices=MTLS_STATUS_CHOICES,
        default="missing",
    )
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["namespace", "pod_name"]
        unique_together = [("cluster", "namespace", "pod_name")]

    def __str__(self):
        return f"{self.namespace}/{self.pod_name}"


class NetworkPolicyEvent(models.Model):
    """L3/L4 eBPF or L7 Envoy network enforcement event."""

    LAYER_CHOICES = [
        ("ebpf", "eBPF / Cilium"),
        ("envoy", "Envoy"),
    ]
    ACTION_CHOICES = [
        ("allow", "Allow"),
        ("drop", "Drop"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="network_policy_events",
    )
    cluster = models.ForeignKey(
        ClusterRegistration,
        on_delete=models.CASCADE,
        related_name="network_events",
        null=True,
        blank=True,
    )
    layer = models.CharField(max_length=16, choices=LAYER_CHOICES, db_index=True)
    action = models.CharField(max_length=8, choices=ACTION_CHOICES, db_index=True)
    source_ref = models.CharField(max_length=512)
    dest_ref = models.CharField(max_length=512)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.layer}:{self.action} {self.source_ref}->{self.dest_ref}"


class EmbeddingInspectionJob(models.Model):
    """River queue embedding inspection job."""

    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("clean", "Clean"),
        ("quarantined", "Quarantined"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="embedding_inspection_jobs",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="queued", db_index=True)
    collection = models.CharField(max_length=255, blank=True)
    anomaly_score = models.FloatField(default=0.0)
    payload_hash = models.CharField(max_length=64, blank=True)
    quarantine_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.collection or 'embedding'} ({self.status})"
