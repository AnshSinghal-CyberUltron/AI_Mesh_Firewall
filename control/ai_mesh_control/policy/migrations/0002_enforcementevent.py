# Generated migration: EnforcementEvent for Phase 2

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("policy", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="EnforcementEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=16)),
                ("user_id", models.IntegerField(blank=True, null=True)),
                ("endpoint_id", models.IntegerField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "policy",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="enforcement_events",
                        to="policy.policy",
                    ),
                ),
                (
                    "rule",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="enforcement_events",
                        to="policy.rule",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
