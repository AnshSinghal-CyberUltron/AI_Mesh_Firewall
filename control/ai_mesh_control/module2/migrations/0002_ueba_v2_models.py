import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auth_api", "0008_remove_aiguardx_roles"),
        ("core", "0029_gatewayapikey_ueba_v2_fields"),
        ("module2", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrgUebaSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                # graduation_min_requests / graduation_min_days intentionally omitted:
                # single org knob is behavior_profile_prompt_target (migration 0006).
                ("llm_triage_enabled", models.BooleanField(default=True)),
                ("llm_triage_min_traditional_score", models.FloatField(default=0.45)),
                ("high_risk_threshold", models.FloatField(default=0.70)),
                ("medium_risk_threshold", models.FloatField(default=0.35)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ueba_settings",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "verbose_name": "Org UEBA Settings",
                "verbose_name_plural": "Org UEBA Settings",
            },
        ),
        migrations.CreateModel(
            name="ApiKeyBehaviorBaseline",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("window_days", models.PositiveIntegerField(default=7)),
                ("avg_requests_per_hour", models.FloatField(default=0.0)),
                ("std_requests_per_hour", models.FloatField(default=0.0)),
                ("avg_block_rate", models.FloatField(default=0.0)),
                ("avg_redact_rate", models.FloatField(default=0.0)),
                ("typical_models", models.JSONField(blank=True, default=list)),
                ("typical_threat_types", models.JSONField(blank=True, default=dict)),
                ("sample_count", models.PositiveIntegerField(default=0)),
                ("computed_at", models.DateTimeField(auto_now=True)),
                (
                    "gateway_api_key",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ueba_baseline",
                        to="core.gatewayapikey",
                    ),
                ),
            ],
            options={
                "ordering": ["-computed_at"],
            },
        ),
        migrations.CreateModel(
            name="ApiKeyRiskAssessment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("computed_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "ueba_mode",
                    models.CharField(
                        choices=[("learning", "Learning"), ("active", "Active")],
                        default="learning",
                        max_length=16,
                    ),
                ),
                ("traditional_score", models.FloatField(default=0.0)),
                ("llm_score", models.FloatField(blank=True, null=True)),
                ("final_score", models.FloatField(default=0.0)),
                (
                    "risk_band",
                    models.CharField(
                        choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")],
                        default="low",
                        max_length=16,
                    ),
                ),
                ("score_breakdown", models.JSONField(blank=True, default=dict)),
                (
                    "llm_verdict",
                    models.CharField(
                        choices=[
                            ("benign", "Benign"),
                            ("suspicious", "Suspicious"),
                            ("malicious", "Malicious"),
                            ("skipped", "Skipped"),
                        ],
                        default="skipped",
                        max_length=16,
                    ),
                ),
                ("llm_confidence", models.FloatField(blank=True, null=True)),
                ("llm_reasoning", models.TextField(blank=True, default="")),
                ("llm_recommended_action", models.CharField(blank=True, default="", max_length=32)),
                ("graduation_progress", models.JSONField(blank=True, default=dict)),
                (
                    "gateway_api_key",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ueba_assessments",
                        to="core.gatewayapikey",
                    ),
                ),
            ],
            options={
                "ordering": ["-computed_at"],
                "indexes": [
                    models.Index(fields=["gateway_api_key", "-computed_at"], name="module2_api_gateway_7f0e2a_idx"),
                ],
            },
        ),
    ]
