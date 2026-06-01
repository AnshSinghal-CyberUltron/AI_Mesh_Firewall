from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_firewallconfig_tier2_execution_modes"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmmodelconfig",
            name="encrypted_api_key",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Organization-provided provider API key encrypted at rest.",
            ),
        ),
        migrations.AddField(
            model_name="llmmodelconfig",
            name="api_key_last4",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Last 4 characters of the stored API key for operator verification.",
                max_length=8,
            ),
        ),
    ]
