"""Phase 0c C-2: incrementally maintained hourly analytics facts."""

from django.db import models


class AnalyticsHourlyRowFact(models.Model):
    """Per-org per-hour per-action row counters for SOC KPIs."""

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="analytics_hourly_row_facts",
    )
    hour = models.DateTimeField(db_index=True)
    action = models.CharField(max_length=16)
    n = models.PositiveIntegerField(default=0)
    latency_sum = models.FloatField(default=0.0)
    critical_n = models.PositiveIntegerField(default=0)
    lat_0_50 = models.PositiveIntegerField(default=0)
    lat_50_100 = models.PositiveIntegerField(default=0)
    lat_100_250 = models.PositiveIntegerField(default=0)
    lat_250_500 = models.PositiveIntegerField(default=0)
    lat_500_1000 = models.PositiveIntegerField(default=0)
    lat_1s = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "policy"
        unique_together = [("organization", "hour", "action")]
        indexes = [models.Index(fields=["organization", "hour"])]


class AnalyticsHourlyRequestFact(models.Model):
    """Per-org per-hour per-request_key max rank/risk (C-9)."""

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="analytics_hourly_request_facts",
    )
    hour = models.DateTimeField(db_index=True)
    request_key = models.CharField(max_length=256)
    max_rank = models.PositiveSmallIntegerField(default=1)
    max_risk = models.FloatField(default=0.0)

    class Meta:
        app_label = "policy"
        unique_together = [("organization", "hour", "request_key")]
        indexes = [models.Index(fields=["organization", "hour"])]


class AnalyticsHourlyGroupFact(models.Model):
    """Per-org per-hour GROUP BY of classifier inputs (module + vector)."""

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="analytics_hourly_group_facts",
    )
    hour = models.DateTimeField(db_index=True)
    action = models.CharField(max_length=16)
    meta = models.JSONField(default=dict)
    n = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "policy"
        indexes = [models.Index(fields=["organization", "hour"])]


class AnalyticsRollupWatermark(models.Model):
    organization = models.OneToOneField(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="analytics_rollup_watermark",
    )
    covered_from = models.DateTimeField()
    covered_to = models.DateTimeField()
    refreshed_at = models.DateTimeField()

    class Meta:
        app_label = "policy"
