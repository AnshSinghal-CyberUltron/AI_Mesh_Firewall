"""Tests for SOC analytics enforcement-rate calculations."""

from django.test import SimpleTestCase

from policy.analytics_views import _enforcement_rate, _performance_status


class AnalyticsEffectivenessTests(SimpleTestCase):
    def test_enforcement_rate_counts_redact_as_enforced(self):
        # PII category: 14 violations, all redacted, 0 blocked → 100% not 0%
        self.assertEqual(_enforcement_rate(0, 14, 14), 100.0)

    def test_enforcement_rate_mixed_actions(self):
        self.assertEqual(_enforcement_rate(131, 100, 388), round((231 / 388) * 100, 1))

    def test_enforcement_rate_empty_window(self):
        self.assertEqual(_enforcement_rate(0, 0, 0), 0)

    def test_enforcement_rate_counts_monitor_as_enforced(self):
        # PII monitor-mode violations should not show 0% when nothing was blocked
        self.assertEqual(_enforcement_rate(0, 0, 3, monitored=3), 100.0)

    def test_enforcement_rate_mixed_with_monitor(self):
        self.assertEqual(_enforcement_rate(2, 1, 10, monitored=4), 70.0)

    def test_performance_status_thresholds(self):
        self.assertEqual(_performance_status(99.0), "EXCELLENT")
        self.assertEqual(_performance_status(96.0), "GOOD")
        self.assertEqual(_performance_status(92.0), "NEEDS REVIEW")
        self.assertEqual(_performance_status(50.0), "LOW")
