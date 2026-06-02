"""Per-tier enforcement action for the MCP scan-control matrix + a first-class
'monitor' audit decision.

Adds ``MCPScanControl.action`` so Tier-1 and Tier-2 can enforce INDEPENDENTLY
(e.g. Tier-1 block, Tier-2 monitor) instead of sharing the server/tool-level
``default_scan_action`` / ``scan_action``. Default ``inherit`` makes this a
no-op for every existing row (it defers to the same server/tool action the
gateway already resolves today), so there is zero behaviour change on upgrade.

Also widens ``MCPEvent.decision`` to include ``monitor`` (detect + tag + allow,
distinct from a clean ``allow``).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mcp_connector", "0012_rename_presidio_to_scan"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpscancontrol",
            name="action",
            field=models.CharField(
                max_length=8,
                choices=[
                    ("inherit", "Inherit from server/tool default"),
                    ("monitor", "Monitor (detect + tag, allow)"),
                    ("redact", "Redact"),
                    ("block", "Block"),
                ],
                default="inherit",
                help_text=(
                    "Enforcement action for THIS tier+direction+scope row, "
                    "independent per tier. 'inherit' defers to "
                    "MCPToolRegistration.scan_action then "
                    "MCPServerRegistration.default_scan_action then 'monitor'. "
                    "Precedence: block > redact > monitor; a Tier-1 block "
                    "short-circuits Tier-2."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="mcpevent",
            name="decision",
            field=models.CharField(
                max_length=16,
                default="allow",
                choices=[
                    ("allow", "Allow"),
                    ("block", "Block"),
                    ("redact", "Redact"),
                    ("monitor", "Monitor"),
                    ("error", "Error"),
                ],
            ),
        ),
    ]
