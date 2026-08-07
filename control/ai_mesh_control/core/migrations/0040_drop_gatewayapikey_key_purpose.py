"""Drop orphaned key_purpose column left by older UEBA migrations.

The ORM no longer declares key_purpose (simulator identity uses
project_id/name). Shared DBs that still have NOT NULL key_purpose break
GatewayAPIKey inserts with IntegrityError.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0039_drop_gatewayapikey_ueba_graduation_days"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE core_gatewayapikey DROP COLUMN IF EXISTS key_purpose;",
            ],
            reverse_sql=migrations.RunSQL.noop,
            state_operations=[],
        ),
    ]
