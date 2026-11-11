"""GW36 S2: per-organization gateway-v2 plan documents, read by the state projector."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auth_api", "0010_userprofile_is_platform_operator_and_more"),
        ("core", "0040_rewrite_to_redact"),
    ]

    operations = [
        migrations.CreateModel(
            name="V2PlanDocument",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("body", models.TextField()),
                ("revision", models.BigIntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="v2_plan_document",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={"db_table": "core_v2_plan_document"},
        ),
    ]
