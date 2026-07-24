"""Regression tests for alert/anomaly deduplication safeguards."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from auth.models import Organization
from module2.models import AlertRule, AnomalyRule
from module2.tasks import evaluate_alert_rules, run_anomaly_detection
from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent, SecurityIncident


class TaskDedupeTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Dedupe Org", slug="dedupe-org")

    def test_evaluate_alert_rules_dedupes_open_alert_incidents(self):
        AlertRule.objects.create(
            organization=self.org,
            name="High block rate",
            metric="block_rate",
            operator="gt",
            threshold=10,
            window_seconds=3600,
            severity="high",
            enabled=True,
        )
        for _ in range(3):
            EnforcementEvent.objects.create(
                organization=self.org,
                action=ACTION_BLOCK,
                metadata={"threat_type": "prompt_injection"},
            )

        evaluate_alert_rules(org_id=self.org.id)
        evaluate_alert_rules(org_id=self.org.id)

        incidents = SecurityIncident.objects.filter(
            organization=self.org,
            title="Alert: High block rate",
            status="open",
        )
        self.assertEqual(incidents.count(), 1)

    def test_run_anomaly_detection_dedupes_open_anomaly_incidents(self):
        AnomalyRule.objects.create(
            organization=self.org,
            name="Traffic spike",
            scope="org",
            metric="event_rate",
            z_score_threshold=-1.0,  # force trigger for deterministic test
            baseline_window_hours=24,
            enabled=True,
        )
        now = timezone.now()
        for i in range(30):
            EnforcementEvent.objects.create(
                organization=self.org,
                action=ACTION_BLOCK,
                metadata={"threat_type": "volume_spike"},
                created_at=now - timedelta(minutes=i),
            )

        run_anomaly_detection(org_id=self.org.id)
        run_anomaly_detection(org_id=self.org.id)

        incidents = SecurityIncident.objects.filter(
            organization=self.org,
            title="Anomaly: Traffic spike",
            status="open",
        )
        self.assertEqual(incidents.count(), 1)
