"""Scanner/org graduation day columns are not part of Module 2.

Kept as graph node ``0002 → 0003 → 0004``. Prompt/request threshold is only
org ``behavior_profile_prompt_target`` (see ``0006``).

Idempotent DROP here (not a new 0008 file) cleans shared DBs that applied an
older 0002/0003 which created graduation_* / scanner_graduation_* columns.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("module2", "0002_ueba_v2_models"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE module2_orguebasettings "
                "DROP COLUMN IF EXISTS graduation_min_requests, "
                "DROP COLUMN IF EXISTS graduation_min_days, "
                "DROP COLUMN IF EXISTS scanner_graduation_min_requests, "
                "DROP COLUMN IF EXISTS scanner_graduation_min_days;"
            ),
            reverse_sql=migrations.RunSQL.noop,
            state_operations=[],
        ),
    ]
