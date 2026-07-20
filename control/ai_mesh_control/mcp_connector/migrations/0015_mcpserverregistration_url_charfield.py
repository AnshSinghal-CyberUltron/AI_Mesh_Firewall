# Generated manually for P4.13 — accept ws:// / wss:// on MCPServerRegistration.url

from django.core.validators import MaxLengthValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mcp_connector", "0014_rename_mcp_scan_org_tier_idx_mcp_connect_organiz_29f98f_idx_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mcpserverregistration",
            name="url",
            field=models.CharField(
                blank=True,
                default="",
                help_text="MCP server endpoint URL (http/https/ws/wss for remote; blank for stdio)",
                max_length=2048,
                validators=[MaxLengthValidator(2048)],
            ),
        ),
    ]
