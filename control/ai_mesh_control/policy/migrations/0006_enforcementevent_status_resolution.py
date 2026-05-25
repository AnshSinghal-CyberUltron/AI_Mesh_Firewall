"""
Migration: add incident lifecycle fields to EnforcementEvent and create Notification model.

Data migration:
  - Events with action in ('redact', 'monitor') are set to incident_status='resolved'
    because they were never "investigating" threats.
  - Events with action='block' keep the default 'investigating'.
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def set_initial_status(apps, schema_editor):
    EnforcementEvent = apps.get_model("policy", "EnforcementEvent")
    EnforcementEvent.objects.filter(action__in=["redact", "monitor"]).update(
        incident_status="resolved"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("policy", "0005_vectorcollectionpolicy"),
    ]

    operations = [
        # 1. Add incident_status with default 'investigating'
        migrations.AddField(
            model_name="enforcementevent",
            name="incident_status",
            field=models.CharField(
                choices=[
                    ("investigating", "Investigating"),
                    ("escalated", "Escalated"),
                    ("resolved", "Resolved"),
                ],
                db_index=True,
                default="investigating",
                max_length=16,
            ),
        ),
        # 2. Add escalation tracking fields
        migrations.AddField(
            model_name="enforcementevent",
            name="escalated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="enforcementevent",
            name="escalated_by_id",
            field=models.IntegerField(blank=True, null=True),
        ),
        # 3. Add resolution tracking fields
        migrations.AddField(
            model_name="enforcementevent",
            name="resolved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="enforcementevent",
            name="resolved_by_id",
            field=models.IntegerField(blank=True, null=True),
        ),
        # 4. Data migration: redact/monitor events → resolved
        migrations.RunPython(set_initial_status, migrations.RunPython.noop),
        # 5. Create Notification model
        migrations.CreateModel(
            name="Notification",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "type",
                    models.CharField(
                        choices=[("escalation", "Escalation"), ("resolution", "Resolution")],
                        max_length=16,
                    ),
                ),
                (
                    "enforcement_event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to="policy.enforcementevent",
                    ),
                ),
                ("recipient_id", models.IntegerField(db_index=True)),
                ("message", models.CharField(blank=True, max_length=512)),
                ("read", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
