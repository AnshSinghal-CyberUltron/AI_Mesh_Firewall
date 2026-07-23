import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("auth_api", "0008_remove_aiguardx_roles"),
        ("policy", "0030_compliance_tag_catalog"),
    ]

    operations = [
        migrations.CreateModel(
            name="Playbook",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("steps", models.JSONField(blank=True, default=list)),
                ("enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="playbooks",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="AlertRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                (
                    "metric",
                    models.CharField(
                        choices=[
                            ("block_rate", "Block Rate"),
                            ("pii_rate", "PII Rate"),
                            ("tier2_score", "Tier-2 Score"),
                            ("anomaly_z", "Anomaly Z-Score"),
                            ("incident_count", "Incident Count"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "operator",
                    models.CharField(
                        choices=[
                            ("gt", "Greater Than"),
                            ("lt", "Less Than"),
                            ("gte", "Greater Than or Equal"),
                            ("lte", "Less Than or Equal"),
                        ],
                        default="gt",
                        max_length=8,
                    ),
                ),
                ("threshold", models.FloatField()),
                ("window_seconds", models.PositiveIntegerField(default=300)),
                (
                    "severity",
                    models.CharField(
                        choices=[
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("critical", "Critical"),
                        ],
                        default="medium",
                        max_length=16,
                    ),
                ),
                ("enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="alert_rules",
                        to="auth_api.organization",
                    ),
                ),
                (
                    "playbook",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="alert_rules",
                        to="module2.playbook",
                    ),
                ),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="PlaybookRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "trigger",
                    models.CharField(
                        choices=[("manual", "Manual"), ("alert", "Alert"), ("anomaly", "Anomaly")],
                        default="manual",
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("running", "Running"), ("success", "Success"), ("failed", "Failed")],
                        default="running",
                        max_length=16,
                    ),
                ),
                ("result", models.JSONField(blank=True, default=dict)),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "playbook",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="runs",
                        to="module2.playbook",
                    ),
                ),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.CreateModel(
            name="AnomalyRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(blank=True, default="", max_length=255)),
                (
                    "scope",
                    models.CharField(
                        choices=[("org", "Organization"), ("agent", "Agent"), ("model", "Model")],
                        default="org",
                        max_length=16,
                    ),
                ),
                ("scope_id", models.CharField(blank=True, default="", max_length=128)),
                ("metric", models.CharField(default="event_rate", max_length=64)),
                ("z_score_threshold", models.FloatField(default=3.0)),
                ("baseline_window_hours", models.PositiveIntegerField(default=168)),
                ("enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="anomaly_rules",
                        to="auth_api.organization",
                    ),
                ),
                (
                    "playbook",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="anomaly_rules",
                        to="module2.playbook",
                    ),
                ),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="ThreatIntelEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "source",
                    models.CharField(
                        choices=[("auto", "Auto"), ("manual", "Manual"), ("feed", "Feed")],
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("threat_type", models.CharField(max_length=128)),
                ("indicator", models.TextField(help_text="Regex pattern, IP, or prompt fingerprint")),
                ("owasp_code", models.CharField(blank=True, default="", max_length=32)),
                ("confidence", models.FloatField(default=0.8)),
                ("auto_block", models.BooleanField(default=False)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="threat_intel_entries",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="AlertFiring",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fired_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("current_value", models.FloatField()),
                ("message", models.CharField(blank=True, max_length=512)),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="firings",
                        to="module2.alertrule",
                    ),
                ),
                (
                    "linked_incident",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="alert_firings",
                        to="policy.securityincident",
                    ),
                ),
            ],
            options={"ordering": ["-fired_at"]},
        ),
    ]
