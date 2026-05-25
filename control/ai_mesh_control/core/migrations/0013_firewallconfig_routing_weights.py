"""Add routing governance weight fields to FirewallConfig."""

from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_firewallconfig_organization_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="routing_risk_weight",
            field=models.FloatField(
                default=0.30,
                help_text="Weight for risk dimension in model routing (0-1).",
                validators=[
                    django.core.validators.MinValueValidator(0.0),
                    django.core.validators.MaxValueValidator(1.0),
                ],
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="routing_cost_weight",
            field=models.FloatField(
                default=0.20,
                help_text="Weight for cost dimension in model routing (0-1).",
                validators=[
                    django.core.validators.MinValueValidator(0.0),
                    django.core.validators.MaxValueValidator(1.0),
                ],
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="routing_latency_weight",
            field=models.FloatField(
                default=0.20,
                help_text="Weight for latency dimension in model routing (0-1).",
                validators=[
                    django.core.validators.MinValueValidator(0.0),
                    django.core.validators.MaxValueValidator(1.0),
                ],
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="routing_priority_weight",
            field=models.FloatField(
                default=0.30,
                help_text="Weight for priority dimension in model routing (0-1).",
                validators=[
                    django.core.validators.MinValueValidator(0.0),
                    django.core.validators.MaxValueValidator(1.0),
                ],
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="default_data_sensitivity",
            field=models.CharField(
                choices=[
                    ("public", "Public"),
                    ("internal", "Internal"),
                    ("confidential", "Confidential"),
                    ("restricted", "Restricted"),
                ],
                default="internal",
                help_text="Default data sensitivity level for routing decisions.",
                max_length=16,
            ),
        ),
    ]
