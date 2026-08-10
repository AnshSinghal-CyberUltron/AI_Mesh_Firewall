"""Merge core 0037 leaves; Module 2: DROP orphan key_purpose / ueba_graduation_days IF EXISTS."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_merge_20260630_0645"),
        ("core", "0037_firewallconfig_mcp_policy_only_enforcement"),
    ]

    operations = [
        # Module 2: no separate 0039/0040 files — cleanup on this merge leaf
        migrations.RunSQL(
            sql=[
                "ALTER TABLE core_gatewayapikey DROP COLUMN IF EXISTS ueba_graduation_days;",
                "ALTER TABLE core_gatewayapikey DROP COLUMN IF EXISTS key_purpose;",
            ],
            reverse_sql=migrations.RunSQL.noop,
            state_operations=[],
        ),
    ]
