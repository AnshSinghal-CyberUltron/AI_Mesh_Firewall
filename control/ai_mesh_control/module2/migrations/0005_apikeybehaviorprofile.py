# Generated manually for UEBA behavior profile

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_gatewayapikey_encrypted_secret"),
        ("module2", "0004_rename_module2_api_gateway_7f0e2a_idx_module2_api_gateway_ee42c6_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiKeyBehaviorProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("prompt_samples", models.JSONField(blank=True, default=list)),
                ("sample_count", models.PositiveIntegerField(default=0)),
                ("profile_built_at", models.DateTimeField(blank=True, null=True)),
                ("expected_use_case", models.TextField(blank=True, default="")),
                (
                    "behavior_class",
                    models.CharField(
                        choices=[
                            ("prod_app", "Production App"),
                            ("scanner", "Security Scanner"),
                            ("dev_test", "Dev / Test"),
                            ("unknown", "Unknown"),
                        ],
                        default="unknown",
                        max_length=32,
                    ),
                ),
                ("risk_prediction", models.TextField(blank=True, default="")),
                ("llm_confidence", models.FloatField(blank=True, null=True)),
                ("profile_version", models.PositiveIntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "gateway_api_key",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ueba_behavior_profile",
                        to="core.gatewayapikey",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
            },
        ),
    ]
