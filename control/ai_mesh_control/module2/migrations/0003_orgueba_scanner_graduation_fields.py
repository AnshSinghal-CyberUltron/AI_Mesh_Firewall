"""Module 2: graph 0002→0003; DROP legacy org graduation_* columns IF EXISTS (prompt-target only)."""

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
