"""
Phase-0 CC-1: tag EnforcementEvent rows with an operational event_class
discriminator and add the composite index used by SOC triage queries.

The column is added with default="enforcement" so the migration backfills
every pre-existing row in a single statement (Postgres ADD COLUMN ... DEFAULT
is constant-time on PG 11+). No data migration step required.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0021_vector_provider_choices_and_attack_vault"),
    ]

    operations = [
        migrations.AddField(
            model_name="enforcementevent",
            name="event_class",
            field=models.CharField(
                max_length=64,
                default="enforcement",
                db_index=True,
                help_text=(
                    "Operational event discriminator. Gateway-owned protocol "
                    "constant (see gateway/ai_mesh_gateway/telemetry_ops.py)."
                ),
            ),
        ),
        migrations.AddIndex(
            model_name="enforcementevent",
            index=models.Index(
                fields=["organization", "event_class", "-created_at"],
                name="ev_org_evclass_ts_idx",
            ),
        ),
    ]
