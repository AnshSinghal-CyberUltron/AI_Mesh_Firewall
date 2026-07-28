"""Drop the orphaned UEBA-v2 columns from core_gatewayapikey.

These columns (from a `ueba_v2` lineage that was never checked into this repo —
migrations `0029_gatewayapikey_ueba_v2_fields` / `0036_alter_gatewayapikey_key_purpose_and_more`
exist only in some prod DBs, not in this codebase) are not referenced by any current
model, serializer, admin, or view. They caused NOT-NULL insert failures on key
creation. This removes the UEBA drift entirely so the DB matches the ORM model.

Raw ``DROP COLUMN IF EXISTS`` with ``state_operations=[]``: the Django model never
declared these fields, so migration STATE needs no change — only the physical DB
columns are dropped. Idempotent (safe on DBs that never had them). ``key_purpose``
is intentionally left in place (separate concept, retains a DB default).
"""

from django.db import migrations

_UEBA_COLUMNS = (
    "ueba_mode",
    "ueba_graduation_requests",
    "ueba_graduation_days",
    "ueba_lifetime_request_count",
    "ueba_baseline_locked_at",
)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_firewallconfig_mcp_policy_only_enforcement"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                f"ALTER TABLE core_gatewayapikey DROP COLUMN IF EXISTS {col};"
                for col in _UEBA_COLUMNS
            ],
            reverse_sql=migrations.RunSQL.noop,
            state_operations=[],
        ),
    ]
