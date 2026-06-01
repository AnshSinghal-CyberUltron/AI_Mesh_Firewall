"""Drop the legacy contextforge_server_id column (DECISION-D Phase 0).

ContextForge has been removed; the column is no longer referenced anywhere in
code. We use RemoveField so Django emits a DROP COLUMN.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("mcp_connector", "0005_remove_global_name_unique_add_per_org"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="mcpserverregistration",
            name="contextforge_server_id",
        ),
    ]
