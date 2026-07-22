# Generated manually for MCPEvent.decision += scan_skipped (choices-only; no DB ALTER needed for VARCHAR).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mcp_connector", "0015_mcpserverregistration_url_charfield"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mcpevent",
            name="decision",
            field=models.CharField(
                choices=[
                    ("allow", "Allow"),
                    ("block", "Block"),
                    ("redact", "Redact"),
                    ("monitor", "Monitor"),
                    ("error", "Error"),
                    ("scan_skipped", "Scan Skipped"),
                ],
                default="allow",
                max_length=16,
            ),
        ),
    ]
