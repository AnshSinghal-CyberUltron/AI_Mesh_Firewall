# Generated for Phase 0c C-2 hourly analytics facts

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auth_api", "0010_userprofile_is_platform_operator_and_more"),
        ("policy", "0040_restore_chroma_vector_db_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnalyticsHourlyRowFact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("hour", models.DateTimeField(db_index=True)),
                ("action", models.CharField(max_length=16)),
                ("n", models.PositiveIntegerField(default=0)),
                ("latency_sum", models.FloatField(default=0.0)),
                ("critical_n", models.PositiveIntegerField(default=0)),
                ("lat_0_50", models.PositiveIntegerField(default=0)),
                ("lat_50_100", models.PositiveIntegerField(default=0)),
                ("lat_100_250", models.PositiveIntegerField(default=0)),
                ("lat_250_500", models.PositiveIntegerField(default=0)),
                ("lat_500_1000", models.PositiveIntegerField(default=0)),
                ("lat_1s", models.PositiveIntegerField(default=0)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analytics_hourly_row_facts",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "unique_together": {("organization", "hour", "action")},
            },
        ),
        migrations.CreateModel(
            name="AnalyticsHourlyRequestFact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("hour", models.DateTimeField(db_index=True)),
                ("request_key", models.CharField(max_length=256)),
                ("max_rank", models.PositiveSmallIntegerField(default=1)),
                ("max_risk", models.FloatField(default=0.0)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analytics_hourly_request_facts",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "unique_together": {("organization", "hour", "request_key")},
            },
        ),
        migrations.CreateModel(
            name="AnalyticsHourlyGroupFact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("hour", models.DateTimeField(db_index=True)),
                ("action", models.CharField(max_length=16)),
                ("meta", models.JSONField(default=dict)),
                ("n", models.PositiveIntegerField(default=0)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analytics_hourly_group_facts",
                        to="auth_api.organization",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AnalyticsRollupWatermark",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("covered_from", models.DateTimeField()),
                ("covered_to", models.DateTimeField()),
                ("refreshed_at", models.DateTimeField()),
                (
                    "organization",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analytics_rollup_watermark",
                        to="auth_api.organization",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="analyticshourlyrowfact",
            index=models.Index(fields=["organization", "hour"], name="policy_anal_organiz_row_hour_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticshourlyrequestfact",
            index=models.Index(fields=["organization", "hour"], name="policy_anal_organiz_req_hour_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticshourlygroupfact",
            index=models.Index(fields=["organization", "hour"], name="policy_anal_organiz_grp_hour_idx"),
        ),
    ]
