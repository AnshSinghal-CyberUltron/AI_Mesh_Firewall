"""Drop orphaned ueba_graduation_days if an older 0029 created it.

learning → active is prompt/request count only; calendar days are not a gate.
Idempotent DROP COLUMN IF EXISTS so DBs that never had the column stay fine.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0038_merge_mcp_policy_and_maincopy"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE core_gatewayapikey DROP COLUMN IF EXISTS ueba_graduation_days;",
            ],
            reverse_sql=migrations.RunSQL.noop,
            # Model no longer declares the field — state needs no RemoveField.
            state_operations=[],
        ),
    ]
