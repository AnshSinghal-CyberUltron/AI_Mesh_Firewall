# Generated manually for Module 3 Phase 3 API governance.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("module3", "0002_widen_signature_digest"),
        ("auth_api", "0008_remove_aiguardx_roles"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiQuotaPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tenant_id", models.CharField(db_index=True, max_length=128)),
                (
                    "environment",
                    models.CharField(
                        choices=[("dev", "Dev"), ("staging", "Staging"), ("prod", "Prod")],
                        default="prod",
                        max_length=16,
                    ),
                ),
                ("tokens_per_minute", models.PositiveIntegerField(default=1000)),
                ("tokens_per_day", models.PositiveIntegerField(default=100000)),
                ("enabled", models.BooleanField(default=True)),
                ("denied_paths", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="api_quota_policies",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["tenant_id", "environment"],
                "unique_together": {("organization", "tenant_id", "environment")},
            },
        ),
        migrations.CreateModel(
            name="ApiQuotaUsage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tokens_minute", models.PositiveIntegerField(default=0)),
                ("tokens_day", models.PositiveIntegerField(default=0)),
                ("minute_window_start", models.DateTimeField(blank=True, null=True)),
                ("day_window_start", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="api_quota_usages",
                        to="auth_api.organization",
                    ),
                ),
                (
                    "policy",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="usage",
                        to="module3.apiquotapolicy",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
            },
        ),
        migrations.CreateModel(
            name="ApiGovernanceEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "action",
                    models.CharField(
                        choices=[("allow", "Allow"), ("deny", "Deny")],
                        db_index=True,
                        max_length=8,
                    ),
                ),
                ("tenant_id", models.CharField(blank=True, max_length=128)),
                ("environment", models.CharField(blank=True, max_length=16)),
                ("estimated_tokens", models.PositiveIntegerField(default=0)),
                ("path", models.CharField(blank=True, max_length=512)),
                ("reason", models.TextField(blank=True)),
                ("source", models.CharField(default="envoy_ext_authz", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="api_governance_events",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
