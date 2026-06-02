from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_auditlog_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="mcp_tier2_enabled",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text=(
                    "Per-org override for MCP Tier-2 (Bedrock) after Tier-1 allows. "
                    "None = inherit gateway default; True/False = explicit override."
                ),
                null=True,
            ),
        ),
    ]
