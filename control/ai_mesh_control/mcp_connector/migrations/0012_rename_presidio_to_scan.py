"""Rename the legacy Presidio-named enforcement/audit fields to engine-agnostic
``scan_*`` names. Presidio was removed; these fields drive the Tier-1/Tier-2
scan outcome and carry scan findings, so they are RENAMED (data-preserving),
never dropped — historical events and configured enforcement actions are kept.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("mcp_connector", "0011_mcp_scan_controls"),
    ]

    operations = [
        migrations.RenameField(
            model_name="mcpserverregistration",
            old_name="default_presidio_action",
            new_name="default_scan_action",
        ),
        migrations.RenameField(
            model_name="mcptoolregistration",
            old_name="presidio_action",
            new_name="scan_action",
        ),
        migrations.RenameField(
            model_name="mcpevent",
            old_name="presidio_findings",
            new_name="scan_findings",
        ),
    ]
