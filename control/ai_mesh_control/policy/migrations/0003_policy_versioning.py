# Generated migration: Policy version field and PolicyVersion model

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("policy", "0002_enforcementevent"),
    ]

    operations = [
        migrations.AddField(
            model_name="policy",
            name="version",
            field=models.PositiveIntegerField(default=1, help_text="Incremented on each update for conflict detection"),
        ),
        migrations.CreateModel(
            name="PolicyVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.PositiveIntegerField()),
                ("snapshot", models.JSONField(default=dict, help_text="Policy + rules snapshot")),
                ("comment", models.CharField(blank=True, max_length=512)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="policy_versions_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "policy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="versions", to="policy.policy"
                    ),
                ),
            ],
            options={
                "ordering": ["-version"],
            },
        ),
        migrations.AddConstraint(
            model_name="policyversion",
            constraint=models.UniqueConstraint(fields=("policy", "version"), name="policy_version_unique"),
        ),
    ]
