from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_firewallconfig_rag_guardrails"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="org_tpm_limit",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Org-wide tokens-per-minute ceiling across all keys (0 = disabled).",
            ),
        ),
    ]
