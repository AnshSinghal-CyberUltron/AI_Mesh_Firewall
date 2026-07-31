from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_firewallconfig_mcp_tier2_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="gatewayapikey",
            name="key_purpose",
            field=models.CharField(
                choices=[("production", "Production"), ("test", "Test"), ("simulator", "Simulator")],
                db_index=True,
                default="production",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="gatewayapikey",
            name="ueba_mode",
            field=models.CharField(
                choices=[("learning", "Learning"), ("active", "Active")],
                db_index=True,
                default="learning",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="gatewayapikey",
            name="ueba_graduation_requests",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Override org default minimum requests before active mode.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="gatewayapikey",
            name="ueba_graduation_days",
            field=models.FloatField(
                blank=True,
                help_text="Override org default minimum days before active mode.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="gatewayapikey",
            name="ueba_lifetime_request_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="gatewayapikey",
            name="ueba_baseline_locked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="gatewayapikey",
            name="risk_score",
            field=models.FloatField(
                default=0.0,
                help_text="Unified UEBA final risk score (0.0 = trusted, 1.0 = highest risk). Written by scoring engine.",
                validators=[],
            ),
        ),
    ]
