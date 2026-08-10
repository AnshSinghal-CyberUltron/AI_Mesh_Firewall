import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auth_api", "0008_remove_aiguardx_roles"),
        ("module2", "0006_ueba_risk_calc_settings"),
    ]

    operations = [
        migrations.CreateModel(
            name="ThreatIntelProjectionState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "managed_blocked_keywords",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="Keywords currently managed by Module 2 threat-intel projection.",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="threat_intel_projection_state",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "verbose_name": "Threat Intel Projection State",
                "verbose_name_plural": "Threat Intel Projection States",
            },
        ),
    ]
