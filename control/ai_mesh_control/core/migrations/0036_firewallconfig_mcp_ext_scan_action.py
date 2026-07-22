from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_alter_firewallconfig_rag_redaction_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="mcp_ext_scan_action",
            field=models.CharField(
                choices=[
                    ("tag", "Tag only (observe, never mutate)"),
                    ("redact", "Redact"),
                    ("block", "Block"),
                ],
                default="tag",
                help_text=(
                    "Action applied to traffic through the transparent external MCP "
                    "proxy (/v1/mcp/ext-proxy/<host>). 'tag' ENFORCES NOTHING: findings "
                    "are detected, tagged and emitted, but the payload is never mutated "
                    "and the call is never blocked. Choose 'redact' or 'block' to "
                    "actually enforce on this surface."
                ),
                max_length=16,
            ),
        ),
    ]
