"""Drop legacy org UEBA graduation columns if an older 0002/0003 created them.

Fresh installs (fixed 0002/0003) never create these columns — this migration
is a no-op then. Shared DBs that already applied the old CreateModel/AddField
still need the DROP. Uses IF EXISTS so both cases are safe.

Operators use only ``behavior_profile_prompt_target`` (default 50).
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("module2", "0007_threatintelprojectionstate"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE module2_orguebasettings "
                        "DROP COLUMN IF EXISTS graduation_min_requests, "
                        "DROP COLUMN IF EXISTS graduation_min_days, "
                        "DROP COLUMN IF EXISTS scanner_graduation_min_requests, "
                        "DROP COLUMN IF EXISTS scanner_graduation_min_days;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
