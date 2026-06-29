import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("auth_api", "0008_remove_aiguardx_roles"),
    ]

    operations = [
        migrations.CreateModel(
            name="ModelArtifact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("version", models.CharField(max_length=128)),
                ("image_ref", models.CharField(max_length=512)),
                ("data_sha256", models.CharField(blank=True, max_length=64)),
                ("model_sha256", models.CharField(blank=True, max_length=64)),
                ("signature_digest", models.CharField(blank=True, max_length=512)),
                (
                    "signature_status",
                    models.CharField(
                        choices=[
                            ("unsigned", "Unsigned"),
                            ("pending", "Pending"),
                            ("valid", "Valid"),
                            ("invalid", "Invalid"),
                        ],
                        db_index=True,
                        default="unsigned",
                        max_length=16,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[("ci", "CI/CD"), ("manual", "Manual")],
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="model_artifacts",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
                "unique_together": {("organization", "name", "version")},
            },
        ),
        migrations.CreateModel(
            name="ClusterRegistration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("k8s_version", models.CharField(blank=True, max_length=32)),
                ("cilium_enabled", models.BooleanField(default=True)),
                ("last_heartbeat_at", models.DateTimeField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("healthy", "Healthy"),
                            ("degraded", "Degraded"),
                            ("offline", "Offline"),
                        ],
                        default="healthy",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="k8s_clusters",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
                "unique_together": {("organization", "name")},
            },
        ),
        migrations.CreateModel(
            name="AdmissionDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "result",
                    models.CharField(
                        choices=[("allow", "Allow"), ("deny", "Deny")],
                        db_index=True,
                        max_length=8,
                    ),
                ),
                ("reason", models.TextField(blank=True)),
                ("verified_by", models.CharField(default="gateway", max_length=64)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "artifact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="admission_decisions",
                        to="module3.modelartifact",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="admission_decisions",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="EmbeddingInspectionJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("clean", "Clean"),
                            ("quarantined", "Quarantined"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("collection", models.CharField(blank=True, max_length=255)),
                ("anomaly_score", models.FloatField(default=0.0)),
                ("payload_hash", models.CharField(blank=True, max_length=64)),
                ("quarantine_reason", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="embedding_inspection_jobs",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="NetworkPolicyEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "layer",
                    models.CharField(
                        choices=[("ebpf", "eBPF / Cilium"), ("envoy", "Envoy")],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[("allow", "Allow"), ("drop", "Drop")],
                        db_index=True,
                        max_length=8,
                    ),
                ),
                ("source_ref", models.CharField(max_length=512)),
                ("dest_ref", models.CharField(max_length=512)),
                ("reason", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "cluster",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="network_events",
                        to="module3.clusterregistration",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="network_policy_events",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PipelineRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("commit_sha", models.CharField(blank=True, max_length=64)),
                ("workflow", models.CharField(blank=True, max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("running", "Running"),
                            ("success", "Success"),
                            ("failed", "Failed"),
                        ],
                        default="running",
                        max_length=16,
                    ),
                ),
                ("started_at", models.DateTimeField()),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "artifact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pipeline_runs",
                        to="module3.modelartifact",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pipeline_runs",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["-started_at"],
            },
        ),
        migrations.CreateModel(
            name="WorkloadPod",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("namespace", models.CharField(max_length=128)),
                ("pod_name", models.CharField(max_length=255)),
                (
                    "workload_type",
                    models.CharField(
                        choices=[
                            ("model", "Model"),
                            ("vector_db", "Vector DB"),
                            ("agent", "Agent"),
                        ],
                        default="model",
                        max_length=16,
                    ),
                ),
                ("labels", models.JSONField(blank=True, default=dict)),
                ("sidecar_attached", models.BooleanField(default=False)),
                (
                    "mtls_status",
                    models.CharField(
                        choices=[
                            ("healthy", "Healthy"),
                            ("degraded", "Degraded"),
                            ("missing", "Missing"),
                        ],
                        default="missing",
                        max_length=16,
                    ),
                ),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "cluster",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pods",
                        to="module3.clusterregistration",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="workload_pods",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["namespace", "pod_name"],
                "unique_together": {("cluster", "namespace", "pod_name")},
            },
        ),
    ]
