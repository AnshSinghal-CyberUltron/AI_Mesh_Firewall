"""Merge parallel ``core`` leaf nodes on ``main-copy``.

What this migration does
------------------------
Joins two ``0037_*`` leaves that diverged on this branch:

  * ``0037_merge_20260630_0645`` — main ↔ Module 2 graph merge
  * ``0037_firewallconfig_mcp_policy_only_enforcement`` — MCP flag

Also (no new migration files): idempotent DROP of orphan UEBA columns that
older Module 2 drafts may have left on ``core_gatewayapikey``. Fresh installs
from fixed ``0029``/``0036`` never create them — ``IF EXISTS`` is a no-op then.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_merge_20260630_0645"),
        ("core", "0037_firewallconfig_mcp_policy_only_enforcement"),
    ]

    operations = [
        # Module 2 product: no key_purpose / no ueba_graduation_days on GatewayAPIKey.
        # Keep this on the existing merge leaf — do NOT add 0039/0040 files.
        migrations.RunSQL(
            sql=[
                "ALTER TABLE core_gatewayapikey DROP COLUMN IF EXISTS ueba_graduation_days;",
                "ALTER TABLE core_gatewayapikey DROP COLUMN IF EXISTS key_purpose;",
            ],
            reverse_sql=migrations.RunSQL.noop,
            state_operations=[],
        ),
    ]
